from django.urls import path
from . import views

app_name = "organizacion"
urlpatterns = [
    path("", views.hoy, name="hoy"),
    path("capturar/", views.captura_rapida, name="captura_rapida"),
    path("semana/", views.semana, name="semana"),
    path("calendario/", views.calendario, name="calendario"),
    path("tareas/", views.tarea_list, name="tarea_list"),
    path("tareas/nueva/", views.tarea_create, name="tarea_create"),
    path("tareas/<int:pk>/", views.tarea_detail, name="tarea_detail"),
    path("tareas/<int:pk>/calendario.ics", views.tarea_calendario, name="tarea_calendario"),
    path("tareas/<int:pk>/google-calendario/", views.tarea_google_calendario, name="tarea_google_calendario"),
    path("tareas/<int:pk>/google-tasks/", views.tarea_google_tasks, name="tarea_google_tasks"),
    path("google-tasks/callback/", views.google_tasks_callback, name="google_tasks_callback"),
    path("tareas/<int:pk>/editar/", views.tarea_update, name="tarea_update"),
    path("tareas/<int:pk>/eliminar/", views.tarea_delete, name="tarea_delete"),
    path("tareas/<int:pk>/estado/", views.tarea_estado, name="tarea_estado"),
    path("tareas/<int:pk>/programar/", views.tarea_programar, name="tarea_programar"),
    path("tareas/<int:pk>/subtareas/nueva/", views.subtarea_create, name="subtarea_create"),
    path("subtareas/<int:pk>/alternar/", views.subtarea_toggle, name="subtarea_toggle"),
    path("subtareas/<int:pk>/estado/", views.subtarea_estado, name="subtarea_estado"),
    path("areas/", views.area_list, name="area_list"), path("areas/nueva/", views.area_create, name="area_create"), path("areas/<int:pk>/editar/", views.area_update, name="area_update"),
    path("proyectos/", views.proyecto_list, name="proyecto_list"), path("proyectos/nuevo/", views.proyecto_create, name="proyecto_create"), path("proyectos/<int:pk>/editar/", views.proyecto_update, name="proyecto_update"),
    path("recurrentes/", views.recurrencia_list, name="recurrencia_list"), path("recurrentes/nueva/", views.recurrencia_create, name="recurrencia_create"), path("recurrentes/<int:pk>/editar/", views.recurrencia_update, name="recurrencia_update"),
]
