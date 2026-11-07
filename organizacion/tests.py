import io
import json
from decimal import Decimal
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Categoria, Etiqueta, MovimientoFinanciero, Notificacion, PresupuestoMensual
from core.forms import MovimientoFinancieroForm
from core.views import resumen_presupuesto
from .forms import TareaForm
from .models import Area, Proyecto, Recurrencia, Subtarea, Tarea
from .services import generar_notificaciones_organizacion, generar_tareas_recurrentes, sincronizar_etiqueta_proyecto, sincronizar_etiqueta_tarea


class OrganizationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("organizador", password="test-pass-123")
        self.other = User.objects.create_user("otro", password="test-pass-123")
        permission = Permission.objects.get(codename="access_organization")
        self.user.user_permissions.add(permission)
        self.client.force_login(self.user)

    def test_permission_is_required(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("organizacion:hoy")).status_code, 403)

    def test_financial_only_user_does_not_see_projects_in_movement_form(self):
        form = MovimientoFinancieroForm(user=self.other, tipo=MovimientoFinanciero.Tipo.GASTO)
        self.assertEqual(form.fields["etiquetas"].label, "Etiquetas")
        self.assertNotIn("proyecto", form.fields["etiquetas"].help_text.lower())

    def test_task_finances_use_existing_shared_tag(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Curso")
        tag = sincronizar_etiqueta_tarea(task)
        income = MovimientoFinanciero.objects.create(usuario=self.user, tipo="ingreso", estado="confirmado", monto="100.00", fecha=timezone.localdate(), concepto="Reembolso")
        expense = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="confirmado", monto="35.50", fecha=timezone.localdate(), concepto="Libro")
        pending = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="pendiente", monto="12.00", fecha=timezone.localdate(), concepto="Pendiente")
        removed = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="eliminado", monto="9.00", fecha=timezone.localdate(), concepto="Eliminado")
        unrelated = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="confirmado", monto="80.00", fecha=timezone.localdate(), concepto="Sin etiqueta")
        for movement in (income, expense, pending, removed):
            movement.etiquetas.add(tag)
        response = self.client.get(reverse("organizacion:tarea_detail", args=[task.pk]))
        self.assertEqual(str(response.context["ingresos"]), "100.00")
        self.assertEqual(str(response.context["gastos"]), "35.50")
        self.assertEqual(str(response.context["balance"]), "64.50")
        self.assertContains(response, "Pendiente")
        self.assertNotContains(response, "Eliminado")
        self.assertNotContains(response, "Sin etiqueta")
        self.assertContains(response, f"?etiqueta={tag.pk}")

    def test_financial_only_user_does_not_see_task_finance_panel(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Privada")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("organizacion:tarea_detail", args=[task.pk])).status_code, 403)
        response = self.client.get(reverse("movimiento_gasto_create"))
        self.assertNotContains(response, "Finanzas de esta tarea")

    def test_quick_capture_creates_private_inbox_task(self):
        response = self.client.post(reverse("organizacion:captura_rapida"), {"titulo": "Leer capítulo"})
        self.assertRedirects(response, reverse("organizacion:tarea_list"))
        task = Tarea.objects.get()
        self.assertEqual(task.usuario, self.user)
        self.assertEqual(task.estado, Tarea.Estado.BANDEJA)
        self.assertEqual(task.etiqueta_automatica.nombre, "Leer capítulo")

    def test_other_users_task_is_not_accessible(self):
        task = Tarea.objects.create(usuario=self.other, titulo="Privada")
        self.assertEqual(self.client.get(reverse("organizacion:tarea_detail", args=[task.pk])).status_code, 404)

    def test_all_subtasks_complete_parent_and_reopening_syncs_it(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Proyecto", estado=Tarea.Estado.EN_PROGRESO)
        first = Subtarea.objects.create(tarea=task, titulo="Uno")
        second = Subtarea.objects.create(tarea=task, titulo="Dos", completada=True)
        self.client.post(reverse("organizacion:subtarea_toggle", args=[first.pk]))
        task.refresh_from_db()
        self.assertEqual(task.estado, Tarea.Estado.COMPLETADA)
        self.client.post(reverse("organizacion:subtarea_toggle", args=[second.pk]))
        task.refresh_from_db()
        self.assertEqual(task.estado, Tarea.Estado.BANDEJA)

    def test_recurring_generation_is_idempotent(self):
        rule = Recurrencia.objects.create(usuario=self.user, titulo="Ordenar casa", frecuencia="diaria", proxima_fecha=timezone.localdate(), intervalo=1)
        until = timezone.localdate() + timedelta(days=2)
        generar_tareas_recurrentes(self.user, until)
        generar_tareas_recurrentes(self.user, until)
        self.assertEqual(Tarea.objects.filter(recurrencia=rule).count(), 3)

    def test_notifications_include_today_and_overdue(self):
        today = timezone.localdate()
        Tarea.objects.create(usuario=self.user, titulo="Hoy", fecha_planificada=today, estado=Tarea.Estado.BANDEJA)
        Tarea.objects.create(usuario=self.user, titulo="Vencida", fecha_limite=timezone.now() - timedelta(hours=1), estado=Tarea.Estado.BANDEJA)
        generar_notificaciones_organizacion(self.user, today)
        self.assertEqual(Notificacion.objects.filter(usuario=self.user, clave__startswith="organizacion:").count(), 2)

    def test_task_form_is_minimal_and_creates_named_tag(self):
        form = TareaForm(user=self.user)
        self.assertEqual(list(form.fields), ["titulo", "descripcion", "inicio_planificado", "fin_planificado", "ingreso_estimado", "gasto_estimado"])
        response = self.client.post(reverse("organizacion:tarea_create"), {"titulo": "Electrónica", "descripcion": "Práctica"})
        self.assertEqual(response.status_code, 302)
        task = Tarea.objects.get()
        self.assertEqual(task.etiqueta_automatica.nombre, "Electrónica")
        self.assertEqual(list(task.etiquetas.values_list("nombre", flat=True)), ["Electrónica"])

    def test_task_estimates_appear_on_board_and_detail(self):
        response = self.client.post(reverse("organizacion:tarea_create"), {
            "titulo": "Diseño", "ingreso_estimado": "150.00", "gasto_estimado": "25.50",
        })
        self.assertEqual(response.status_code, 302)
        task = Tarea.objects.get(usuario=self.user)
        self.assertEqual(task.balance_estimado, Decimal("124.50"))
        board = self.client.get(reverse("organizacion:tarea_list"))
        self.assertEqual(board.context["estimated_balance"], Decimal("124.50"))
        self.assertContains(board, "Balance estimado")
        self.assertContains(board, "124,50")
        detail = self.client.get(reverse("organizacion:tarea_detail", args=[task.pk]))
        self.assertContains(detail, "Estimado")
        self.assertContains(detail, "Real")

    def test_board_estimates_respect_filters_and_user(self):
        own = Tarea.objects.create(usuario=self.user, titulo="Trabajo", ingreso_estimado="80.00", gasto_estimado="20.00")
        tag = sincronizar_etiqueta_tarea(own)
        Tarea.objects.create(usuario=self.user, titulo="Otro", gasto_estimado="10.00")
        Tarea.objects.create(usuario=self.other, titulo="Ajeno", ingreso_estimado="1000.00")
        response = self.client.get(reverse("organizacion:tarea_list"), {"etiqueta": tag.pk})
        self.assertEqual(response.context["estimated_income"], Decimal("80.00"))
        self.assertEqual(response.context["estimated_expense"], Decimal("20.00"))
        self.assertEqual(response.context["estimated_balance"], Decimal("60.00"))

    def test_board_reports_only_confirmed_related_movements(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Trabajo")
        tag = sincronizar_etiqueta_tarea(task)
        income = MovimientoFinanciero.objects.create(usuario=self.user, tipo="ingreso", estado="confirmado", monto="90.00", fecha=timezone.localdate(), concepto="Cobro")
        pending = MovimientoFinanciero.objects.create(usuario=self.user, tipo="ingreso", estado="pendiente", monto="40.00", fecha=timezone.localdate(), concepto="Pendiente")
        income.etiquetas.add(tag)
        pending.etiquetas.add(tag)
        response = self.client.get(reverse("organizacion:tarea_list"))
        self.assertContains(response, "Confirmado por etiquetas")
        self.assertContains(response, "Gastos relacionados")
        self.assertEqual(response.context["actual_income"], Decimal("90.00"))
        self.assertEqual(response.context["actual_expense"], Decimal("0"))
        self.assertEqual(response.context["columns"][0]["tasks"][0].ingresos_reales, Decimal("90.00"))
        self.assertEqual(response.context["columns"][0]["tasks"][0].gastos_reales, Decimal("0"))

    def test_board_counts_shared_movement_once_in_summary(self):
        first = Tarea.objects.create(usuario=self.user, titulo="Trabajo A")
        second = Tarea.objects.create(usuario=self.user, titulo="Trabajo B")
        first_tag = sincronizar_etiqueta_tarea(first)
        second_tag = sincronizar_etiqueta_tarea(second)
        expense = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="confirmado", monto="12.50", fecha=timezone.localdate(), concepto="Compartido")
        expense.etiquetas.add(first_tag, second_tag)
        response = self.client.get(reverse("organizacion:tarea_list"))
        self.assertEqual(response.context["actual_expense"], Decimal("12.50"))
        self.assertEqual([task.gastos_reales for task in response.context["columns"][0]["tasks"]], [Decimal("12.50"), Decimal("12.50")])

    def test_completed_board_is_limited_but_summary_includes_history(self):
        completed_at = timezone.now()
        old = Tarea.objects.create(usuario=self.user, titulo="Trabajo antiguo", estado=Tarea.Estado.COMPLETADA, completada_en=completed_at - timedelta(days=30), gasto_estimado="30.00")
        tag = sincronizar_etiqueta_tarea(old)
        expense = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="confirmado", monto="12.50", fecha=timezone.localdate(), concepto="Material antiguo")
        expense.etiquetas.add(tag)
        Tarea.objects.bulk_create([
            Tarea(usuario=self.user, titulo=f"Terminado {index}", estado=Tarea.Estado.COMPLETADA, completada_en=completed_at - timedelta(days=25-index))
            for index in range(25)
        ])
        board = self.client.get(reverse("organizacion:tarea_list"))
        self.assertEqual(board.context["completed_tasks"], 26)
        self.assertEqual(len(board.context["columns"][-1]["tasks"]), 20)
        self.assertNotIn(old.pk, [task.pk for task in board.context["columns"][-1]["tasks"]])
        self.assertEqual(board.context["estimated_expense"], Decimal("30.00"))
        self.assertEqual(board.context["actual_expense"], Decimal("12.50"))
        self.assertContains(board, "Ver todas las finalizadas")
        archive = self.client.get(reverse("organizacion:tareas_finalizadas"), {"page": 2})
        self.assertEqual(archive.context["page"].paginator.count, 26)
        self.assertEqual(len(archive.context["page"]), 6)
        self.assertContains(archive, "Trabajo antiguo")
        filtered = self.client.get(reverse("organizacion:tareas_finalizadas"), {"q": "antiguo", "etiqueta": tag.pk})
        self.assertEqual(filtered.context["page"].paginator.count, 1)
        recent = self.client.get(reverse("organizacion:tareas_finalizadas"), {"desde": timezone.localtime(completed_at - timedelta(days=5)).date().isoformat()})
        self.assertEqual(recent.context["page"].paginator.count, 5)

    def test_completed_archive_requires_organization_permission(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("organizacion:tareas_finalizadas")).status_code, 403)

    def test_task_estimate_does_not_change_budget_or_financial_only_access(self):
        today = timezone.localdate()
        category = Categoria.objects.create(usuario=self.user, nombre="Materiales", tipo=Categoria.Tipo.FINANZAS)
        PresupuestoMensual.objects.create(usuario=self.user, categoria=category, anio=today.year, mes=today.month, monto="200.00")
        expense = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="confirmado", categoria=category, monto="25.00", fecha=today, concepto="Material")
        before = resumen_presupuesto(self.user, today)
        task = Tarea.objects.create(usuario=self.user, titulo="Trabajo", ingreso_estimado="500.00", gasto_estimado="75.00")
        tag = sincronizar_etiqueta_tarea(task)
        expense.etiquetas.add(tag)
        after = resumen_presupuesto(self.user, today)
        self.assertEqual(after["total_presupuesto"], before["total_presupuesto"])
        self.assertEqual(after["total_usado"], before["total_usado"])
        self.assertEqual(after["total_gastos"], before["total_gastos"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("organizacion:tarea_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("presupuesto_list")).status_code, 200)

    def test_estimates_cannot_be_negative(self):
        form = TareaForm({"titulo": "Trabajo", "gasto_estimado": "-1.00"}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("gasto_estimado", form.errors)

    def test_organization_home_redirects_to_tasks(self):
        self.assertRedirects(self.client.get(reverse("organizacion:hoy")), reverse("organizacion:tarea_list"))

    def test_new_task_prefills_start_and_end(self):
        today = timezone.localdate()
        form = TareaForm(user=self.user)
        start = datetime.strptime(form.initial["inicio_planificado"], "%Y-%m-%dT%H:%M")
        end = datetime.strptime(form.initial["fin_planificado"], "%Y-%m-%dT%H:%M")
        self.assertEqual(start.date(), today)
        self.assertEqual(end - start, timedelta(hours=1))
        page = self.client.get(reverse("organizacion:tarea_create"))
        self.assertContains(page, f'value="{start:%Y-%m-%dT%H:%M}"')
        self.assertContains(page, 'data-task-start="true"')

    def test_project_financial_tag_totals_income_and_expense(self):
        project = Proyecto.objects.create(usuario=self.user, nombre="Maestría")
        tag = sincronizar_etiqueta_proyecto(project)
        income = MovimientoFinanciero.objects.create(usuario=self.user, tipo="ingreso", estado="confirmado", monto="100.00", fecha=timezone.localdate(), concepto="Apoyo")
        expense = MovimientoFinanciero.objects.create(usuario=self.user, tipo="gasto", estado="confirmado", monto="35.50", fecha=timezone.localdate(), concepto="Libro")
        income.etiquetas.add(tag)
        expense.etiquetas.add(tag)
        response = self.client.get(reverse("organizacion:proyecto_list"))
        listed = response.context["items"][0]
        self.assertEqual(str(listed.ingresos), "100.00")
        self.assertEqual(str(listed.gastos), "35.50")
        self.assertContains(response, "?etiqueta=")

    def test_drag_endpoint_updates_state(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Mover", estado=Tarea.Estado.BANDEJA)
        response = self.client.post(reverse("organizacion:tarea_estado", args=[task.pk]), {"estado": Tarea.Estado.EN_PROGRESO}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.estado, Tarea.Estado.EN_PROGRESO)

        self.assertIsNotNone(task.inicio_real)

    def test_mobile_state_buttons_submit_with_csrf_and_return_to_selected_tab(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Cambiar en móvil", estado=Tarea.Estado.BANDEJA)
        mobile = Client(enforce_csrf_checks=True)
        mobile.force_login(self.user)
        tab_url = reverse("organizacion:tarea_list") + "?tab=bandeja"
        page = mobile.get(tab_url)
        self.assertContains(page, 'data-mobile-state-form')
        self.assertContains(page, 'name="estado" value="en_progreso"')
        response = mobile.post(
            reverse("organizacion:tarea_estado", args=[task.pk]),
            {"csrfmiddlewaretoken": mobile.cookies["csrftoken"].value, "estado": Tarea.Estado.EN_PROGRESO, "next": tab_url, "mobile": "1"},
        )
        self.assertRedirects(response, reverse("organizacion:tarea_list") + "?tab=en_progreso")
        task.refresh_from_db()
        self.assertEqual(task.estado, Tarea.Estado.EN_PROGRESO)
        self.assertContains(mobile.get(tab_url), 'class="org-mobile-task priority-', count=0)
        self.assertContains(mobile.get(reverse("organizacion:tarea_list") + "?tab=en_progreso"), 'class="org-mobile-task priority-', count=1)

    def test_mobile_subtask_state_button_returns_to_detail(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Trabajo")
        item = Subtarea.objects.create(tarea=task, titulo="Paso uno")
        mobile = Client(enforce_csrf_checks=True)
        mobile.force_login(self.user)
        tab_url = reverse("organizacion:tarea_detail", args=[task.pk]) + "?tab=bandeja"
        page = mobile.get(tab_url)
        self.assertContains(page, 'data-subtask-state-form')
        response = mobile.post(
            reverse("organizacion:subtarea_estado", args=[item.pk]),
            {"csrfmiddlewaretoken": mobile.cookies["csrftoken"].value, "estado": Subtarea.Estado.COMPLETADA, "next": tab_url, "mobile": "1"},
        )
        self.assertRedirects(response, reverse("organizacion:tarea_detail", args=[task.pk]) + "?tab=completada")
        item.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(item.estado, Subtarea.Estado.COMPLETADA)
        self.assertEqual(task.estado, Tarea.Estado.COMPLETADA)
        self.assertContains(mobile.get(reverse("organizacion:tarea_detail", args=[task.pk]) + "?tab=completada"), "Paso uno")

    def test_mobile_task_controls_and_calendar_export(self):
        start = timezone.make_aware(datetime(2026, 11, 3, 9, 30))
        task = Tarea.objects.create(usuario=self.user, titulo="Reunión, revisión", descripcion="Línea uno\nLínea dos", inicio_planificado=start, fin_planificado=start + timedelta(hours=1))
        board = self.client.get(reverse("organizacion:tarea_list"))
        self.assertContains(board, 'data-mobile-tasks')
        self.assertContains(board, 'data-mobile-state-form')
        self.assertContains(board, reverse("organizacion:tarea_calendario", args=[task.pk]))
        response = self.client.get(reverse("organizacion:tarea_calendario", args=[task.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertIn("BEGIN:VCALENDAR\r\n", body)
        self.assertIn(f"DTSTART:{start.astimezone(datetime_timezone.utc):%Y%m%dT%H%M%SZ}", body)
        self.assertIn("SUMMARY:Reunión\\, revisión", body)
        self.assertIn("DESCRIPTION:Línea uno\\nLínea dos", body)
        google = self.client.get(reverse("organizacion:tarea_google_calendario", args=[task.pk]))
        self.assertEqual(google.status_code, 302)
        params = parse_qs(urlparse(google["Location"]).query)
        self.assertEqual(params["text"], [task.titulo])
        self.assertEqual(params["dates"], [f"{start.astimezone(datetime_timezone.utc):%Y%m%dT%H%M%SZ}/{(start + timedelta(hours=1)).astimezone(datetime_timezone.utc):%Y%m%dT%H%M%SZ}"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("organizacion:tarea_calendario", args=[task.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("organizacion:tarea_google_calendario", args=[task.pk])).status_code, 403)

    def test_calendar_export_all_day_and_missing_date(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Entrega", fecha_planificada=timezone.localdate())
        response = self.client.get(reverse("organizacion:tarea_calendario", args=[task.pk]))
        self.assertIn("DTSTART;VALUE=DATE:", response.content.decode("utf-8"))
        google = self.client.get(reverse("organizacion:tarea_google_calendario", args=[task.pk]))
        self.assertEqual(len(parse_qs(urlparse(google["Location"]).query)["dates"][0]), 17)
        task.fecha_planificada = None
        task.save(update_fields=["fecha_planificada"])
        self.assertEqual(self.client.get(reverse("organizacion:tarea_calendario", args=[task.pk])).status_code, 400)
        self.assertEqual(self.client.get(reverse("organizacion:tarea_google_calendario", args=[task.pk])).status_code, 400)

    @override_settings(GOOGLE_TASKS_CLIENT_ID="client-id", GOOGLE_TASKS_CLIENT_SECRET="client-secret")
    def test_google_tasks_oauth_creates_task_with_date_and_times_in_notes(self):
        start = timezone.make_aware(datetime(2026, 11, 3, 9, 30))
        task = Tarea.objects.create(usuario=self.user, titulo="Preparar reunión", inicio_planificado=start, fin_planificado=start + timedelta(hours=2))
        begin = self.client.post(reverse("organizacion:tarea_google_tasks", args=[task.pk]))
        self.assertEqual(begin.status_code, 302)
        self.assertEqual(urlparse(begin["Location"]).netloc, "accounts.google.com")
        state = parse_qs(urlparse(begin["Location"]).query)["state"][0]
        token_response = io.BytesIO(json.dumps({"access_token": "test-token"}).encode())
        api_response = io.BytesIO(b'{"id":"google-task"}')
        api_response.status = 200
        with patch("organizacion.views.urlopen", side_effect=[token_response, api_response]) as send:
            callback = self.client.get(reverse("organizacion:google_tasks_callback"), {"state": state, "code": "auth-code"})
        self.assertRedirects(callback, reverse("organizacion:tarea_detail", args=[task.pk]))
        self.assertEqual(send.call_count, 2)
        payload = json.loads(send.call_args_list[1].args[0].data)
        self.assertEqual(payload["title"], task.titulo)
        self.assertEqual(payload["due"], "2026-11-03T00:00:00.000Z")
        self.assertIn("Inicio: 03/11/2026 09:30", payload["notes"])
        self.assertIn("Fin: 03/11/2026 11:30", payload["notes"])
        self.assertEqual(self.client.get(reverse("organizacion:google_tasks_callback"), {"state": state, "code": "auth-code"}).status_code, 400)

    @override_settings(GOOGLE_TASKS_CLIENT_ID="", GOOGLE_TASKS_CLIENT_SECRET="")
    def test_google_tasks_requires_configuration_and_ownership(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Enviar a Google")
        response = self.client.post(reverse("organizacion:tarea_google_tasks", args=[task.pk]))
        self.assertRedirects(response, reverse("organizacion:tarea_detail", args=[task.pk]))
        self.assertNotIn("google_tasks_oauth", self.client.session)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("organizacion:tarea_google_tasks", args=[task.pk])).status_code, 403)

    @override_settings(GOOGLE_TASKS_CLIENT_ID="client-id", GOOGLE_TASKS_CLIENT_SECRET="client-secret")
    def test_google_tasks_authorization_is_bound_to_the_app_user(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Tarea privada")
        begin = self.client.post(reverse("organizacion:tarea_google_tasks", args=[task.pk]))
        state = parse_qs(urlparse(begin["Location"]).query)["state"][0]
        permission = Permission.objects.get(codename="access_organization")
        self.other.user_permissions.add(permission)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("organizacion:tarea_google_tasks", args=[task.pk])).status_code, 404)
        with patch("organizacion.views.urlopen") as send:
            callback = self.client.get(reverse("organizacion:google_tasks_callback"), {"state": state, "code": "auth-code"})
        self.assertEqual(callback.status_code, 400)
        send.assert_not_called()

    def test_subtasks_are_visible_and_drive_parent_state(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Preparar entrega", estado=Tarea.Estado.BANDEJA)
        item = Subtarea.objects.create(tarea=task, titulo="Revisar bibliografía")
        board = self.client.get(reverse("organizacion:tarea_list"))
        self.assertNotContains(board, "Revisar bibliografía")
        self.assertContains(board, "Abrir tablero de subtareas")
        detail = self.client.get(reverse("organizacion:tarea_detail", args=[task.pk]))
        self.assertContains(detail, "Revisar bibliografía")
        self.assertContains(detail, "data-subtask-board")
        self.assertContains(detail, "data-mobile-subtasks")
        self.assertContains(detail, "data-subtask-state-form")
        moved = self.client.post(reverse("organizacion:subtarea_estado", args=[item.pk]), {"estado": Subtarea.Estado.EN_PROGRESO}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(moved.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.estado, Tarea.Estado.EN_PROGRESO)

        blocked = self.client.post(reverse("organizacion:tarea_estado", args=[task.pk]), {"estado": Tarea.Estado.COMPLETADA}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(blocked.status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.estado, Tarea.Estado.EN_PROGRESO)

    def test_calendar_renders_and_reschedules_task(self):
        today = timezone.localdate()
        task = Tarea.objects.create(usuario=self.user, titulo="Calendario", fecha_planificada=today)
        response = self.client.get(reverse("organizacion:calendario") + f"?mes={today:%Y-%m}")
        self.assertContains(response, "Calendario")
        target = today + timedelta(days=1)
        moved = self.client.post(reverse("organizacion:tarea_programar", args=[task.pk]), {"fecha": target.isoformat()}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(moved.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.fecha_planificada, target)

    def test_calendar_event_spans_start_to_end_and_gantt_shows_progress(self):
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(9, 0)))
        task = Tarea.objects.create(usuario=self.user, titulo="Evento largo", inicio_planificado=start, fin_planificado=start + timedelta(days=2, hours=8), estado=Tarea.Estado.EN_PROGRESO)
        response = self.client.get(reverse("organizacion:calendario") + f"?mes={today:%Y-%m}")
        occurrences = sum(1 for week in response.context["weeks"] for day in week for event in day["tasks"] if event["task"].pk == task.pk)
        self.assertEqual(occurrences, 3)
        self.assertEqual(task.progreso, 50)
        self.assertContains(response, "Avance del trabajo")
        self.assertContains(response, "50%")

    def test_task_can_be_deleted_from_board_endpoint(self):
        task = Tarea.objects.create(usuario=self.user, titulo="Eliminar")
        board = self.client.get(reverse("organizacion:tarea_list"))
        self.assertContains(board, reverse("organizacion:tarea_delete", args=[task.pk]))
        response = self.client.post(reverse("organizacion:tarea_delete", args=[task.pk]))
        self.assertRedirects(response, reverse("organizacion:tarea_list"))
        self.assertFalse(Tarea.objects.filter(pk=task.pk).exists())

