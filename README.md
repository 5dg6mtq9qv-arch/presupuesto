# Finanzas Claras

Finanzas Claras es un gestor web de finanzas personales y tareas, desarrollado con Django y PostgreSQL. Centraliza cuentas, ingresos, gastos, presupuestos, deudas, cuotas, movimientos recurrentes, reportes y análisis asistido por IA en un espacio separado por usuario.

La interfaz está en español, usa la zona horaria `America/Guayaquil` y puede instalarse desde el navegador como una PWA en Android o escritorio.

## Funciones principales

- Dashboard con saldos, resumen financiero y próximos compromisos.
- Recorrido guiado para nuevos usuarios y lista persistente de primeros pasos.
- Ingresos y gastos por cuenta, categoría, método de pago y etiquetas.
- Gastos a crédito que generan automáticamente una deuda y su plan de cuotas.
- Deudas manuales, acreedores, pagos pendientes y pagos confirmados.
- Filtros de deuda por fecha de pago y por fecha de generación.
- Presupuestos mensuales, alertas y sugerencias basadas en el historial.
- Movimientos mensuales recurrentes y generación automática de cuotas.
- Transferencias entre cuentas y ajustes de saldo auditados.
- Importación bancaria CSV con previsualización y control de duplicados.
- Captura de comprobantes mediante imagen, con extracción por IA y revisión manual obligatoria.
- Asistente financiero con consultas, objetivos, recomendaciones y registro rápido de movimientos.
- Análisis por periodo, vista de caja o consumo, exportación CSV e informe PDF.
- Agenda de tareas, notificaciones, perfiles y administración de usuarios.
- Módulo opcional de organización personal con vista Hoy, Kanban de tareas y
  subtareas, etiquetas, calendario mensual, línea de tiempo y recurrencias. Se habilita mediante el
  permiso `organizacion.access_organization`; los superusuarios tienen acceso.
- Las etiquetas pueden compartirse entre tareas, ingresos y gastos sin cambiar
  las reglas de presupuestos, saldos ni deudas.
- Crear o renombrar una tarea crea o sincroniza automáticamente una única
  etiqueta con el mismo título. El formulario solo solicita título,
  descripción, inicio y fin.
- El calendario representa cada tarea como un evento continuo desde inicio
  hasta fin; la línea inferior muestra su duración y porcentaje de avance.
- En móvil y tablet, las tareas y subtareas usan una lista con filtro y botones
  para cambiar de estado; el Kanban con arrastre queda para computadora.
- Registro de auditoría, consumo de IA y estimación orientativa de costos.

## Documentación

| Documento | Contenido |
| --- | --- |
| [Guía de usuario](docs/GUIA_USUARIO.md) | Uso diario, gastos a crédito, deudas, comprobantes, IA, reportes y PWA. |
| [Instalación y despliegue](docs/INSTALACION_Y_DESPLIEGUE.md) | Preparación local, PostgreSQL, migraciones y lista de verificación de producción. |
| [Configuración de IA](docs/CONFIGURACION_IA.md) | Proveedores, claves, permisos, privacidad, consumo y solución de problemas. |
| [Arquitectura](docs/ARQUITECTURA.md) | Componentes, flujos, seguridad, estructura del repositorio y decisiones técnicas. |
| [Modelo de datos](docs/MODELO_DE_DATOS.md) | Entidades, relaciones, estados y reglas de integridad. |
| [Operación y mantenimiento](docs/OPERACION_Y_MANTENIMIENTO.md) | Automatizaciones, pruebas, respaldos, actualización y diagnóstico. |

## Inicio rápido

### Requisitos

- Python 3.11 (versión usada por el entorno actual del proyecto).
- PostgreSQL.
- Herramientas de compilación del sistema si alguna dependencia no dispone de una rueda binaria.

### Preparación

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Crea la base de datos PostgreSQL y ajusta en `.env` las variables `POSTGRES_*`. El usuario de la base debe poder crear esquemas, porque las migraciones organizan las tablas por dominios como `finanzas`, `deudas`, `analisis` y `auditoria`.

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Abre `http://127.0.0.1:8000/`. También existe `/registro/` para crear una cuenta de usuario normal. Al ingresar por primera vez se preparan cuentas, métodos de pago y categorías iniciales.

## Verificación

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```

## Calendarios del teléfono y Google Tasks

En el detalle de una tarea con fecha se puede abrir una propuesta de evento en
Google Calendar o descargar un archivo `.ics` para importarlo en otro calendario.
Ambas opciones usan el inicio y el fin planificados. Son exportaciones puntuales:
editar la tarea después no modifica automáticamente el evento ya agregado.

Para habilitar **Agregar a Google Tasks**, activa la Google Tasks API en Google
Cloud, crea un cliente OAuth de tipo aplicación web y registra
`https://presupuesto.felixiot.site/organizacion/google-tasks/callback/` como URI
de redirección autorizada (usa tu propio dominio si corresponde). Configura
`GOOGLE_TASKS_CLIENT_ID` y `GOOGLE_TASKS_CLIENT_SECRET` en `.env`. Cada envío
pide autorización a la cuenta Google del usuario y crea una tarea en su lista
predeterminada; el servidor no guarda los tokens. Google Tasks solo acepta la
fecha mediante su API, así que las horas de inicio y fin se copian a las notas.

## Tecnologías

- Django 5.2.16.
- PostgreSQL mediante `psycopg`.
- Django templates y vistas renderizadas en servidor.
- Django Unfold para el panel administrativo.
- Pillow para imágenes y comprobantes.
- ReportLab para informes PDF.
- Cryptography/Fernet para cifrar claves de proveedores de IA guardadas en la base.

## Alcance técnico

El proyecto no expone actualmente una API REST pública. La aplicación funciona mediante sesiones Django, formularios con protección CSRF y páginas HTML. El manifiesto y el service worker ofrecen instalación tipo PWA y una pantalla de desconexión, pero los datos financieros requieren conexión y no se almacenan para operar sin internet.

La configuración de desarrollo se encuentra en `taskbudget/settings.py`. Antes de publicar en otro dominio se deben revisar `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, HTTPS, archivos estáticos, archivos multimedia y el servidor de aplicación; consulta la [guía de despliegue](docs/INSTALACION_Y_DESPLIEGUE.md).
