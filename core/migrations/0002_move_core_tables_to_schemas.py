from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                ("CREATE SCHEMA IF NOT EXISTS categorias;", None),
                ("CREATE SCHEMA IF NOT EXISTS tareas;", None),
                ("CREATE SCHEMA IF NOT EXISTS movimientos_financieros;", None),
                ("CREATE SCHEMA IF NOT EXISTS deudas;", None),
                ("CREATE SCHEMA IF NOT EXISTS pagos_deudas;", None),
                ("ALTER TABLE IF EXISTS public.core_categoria SET SCHEMA categorias;", None),
                ("ALTER TABLE IF EXISTS categorias.core_categoria RENAME TO categoria;", None),
                ("ALTER TABLE IF EXISTS public.core_tarea SET SCHEMA tareas;", None),
                ("ALTER TABLE IF EXISTS tareas.core_tarea RENAME TO tarea;", None),
                (
                    "ALTER TABLE IF EXISTS public.core_movimientofinanciero "
                    "SET SCHEMA movimientos_financieros;",
                    None,
                ),
                (
                    "ALTER TABLE IF EXISTS movimientos_financieros.core_movimientofinanciero "
                    "RENAME TO movimiento_financiero;",
                    None,
                ),
                ("ALTER TABLE IF EXISTS public.core_deuda SET SCHEMA deudas;", None),
                ("ALTER TABLE IF EXISTS deudas.core_deuda RENAME TO deuda;", None),
                ("ALTER TABLE IF EXISTS public.core_pagodeuda SET SCHEMA pagos_deudas;", None),
                ("ALTER TABLE IF EXISTS pagos_deudas.core_pagodeuda RENAME TO pago_deuda;", None),
            ],
            reverse_sql=[
                ("CREATE SCHEMA IF NOT EXISTS public;", None),
                ("ALTER TABLE IF EXISTS categorias.categoria RENAME TO core_categoria;", None),
                ("ALTER TABLE IF EXISTS categorias.core_categoria SET SCHEMA public;", None),
                ("ALTER TABLE IF EXISTS tareas.tarea RENAME TO core_tarea;", None),
                ("ALTER TABLE IF EXISTS tareas.core_tarea SET SCHEMA public;", None),
                (
                    "ALTER TABLE IF EXISTS movimientos_financieros.movimiento_financiero "
                    "RENAME TO core_movimientofinanciero;",
                    None,
                ),
                (
                    "ALTER TABLE IF EXISTS movimientos_financieros.core_movimientofinanciero "
                    "SET SCHEMA public;",
                    None,
                ),
                ("ALTER TABLE IF EXISTS deudas.deuda RENAME TO core_deuda;", None),
                ("ALTER TABLE IF EXISTS deudas.core_deuda SET SCHEMA public;", None),
                ("ALTER TABLE IF EXISTS pagos_deudas.pago_deuda RENAME TO core_pagodeuda;", None),
                ("ALTER TABLE IF EXISTS pagos_deudas.core_pagodeuda SET SCHEMA public;", None),
            ],
        ),
    ]
