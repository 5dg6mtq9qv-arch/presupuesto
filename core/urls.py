from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("usuarios/", views.usuario_list, name="usuario_list"),
    path("usuarios/nuevo/", views.usuario_create, name="usuario_create"),
    path("usuarios/<int:pk>/editar/", views.usuario_update, name="usuario_update"),
    path("usuarios/<int:pk>/contrasenia/", views.usuario_password, name="usuario_password"),
    path("categorias/", views.categoria_list, name="categoria_list"),
    path("categorias/<int:pk>/editar/", views.categoria_update, name="categoria_update"),
    path("categorias/<int:pk>/eliminar/", views.categoria_delete, name="categoria_delete"),
    path("tareas/", views.tarea_list, name="tarea_list"),
    path("tareas/nueva/", views.tarea_create, name="tarea_create"),
    path("tareas/<int:pk>/editar/", views.tarea_update, name="tarea_update"),
    path("tareas/<int:pk>/estado/<str:estado>/", views.tarea_estado, name="tarea_estado"),
    path("tareas/<int:pk>/eliminar/", views.tarea_delete, name="tarea_delete"),
    path("movimientos/", views.movimiento_list, name="movimiento_list"),
    path("movimientos/nuevo/", views.movimiento_create, name="movimiento_create"),
    path("movimientos/<int:pk>/editar/", views.movimiento_update, name="movimiento_update"),
    path("movimientos/<int:pk>/eliminar/", views.movimiento_delete, name="movimiento_delete"),
    path("deudas/", views.deuda_list, name="deuda_list"),
    path("deudas/nueva/", views.deuda_create, name="deuda_create"),
    path("deudas/<int:pk>/editar/", views.deuda_update, name="deuda_update"),
    path("deudas/<int:pk>/eliminar/", views.deuda_delete, name="deuda_delete"),
    path("deudas/<int:deuda_id>/pagos/nuevo/", views.pago_create, name="pago_create"),
    path("pagos/<int:pk>/eliminar/", views.pago_delete, name="pago_delete"),
]
