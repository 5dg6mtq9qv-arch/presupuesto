# TaskBudget

Gestor web de finanzas personales construido con Django y PostgreSQL.

## Puesta en marcha

1. Crea la base PostgreSQL indicada por `POSTGRES_DB`.
2. Copia `.env.example` a `.env` y reemplaza los valores de ejemplo.
3. Exporta las variables de `.env` en el servicio que ejecuta Django.
4. Ejecuta las migraciones y arranca el servidor:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

La pantalla `/registro/` crea el primer usuario y prepara automáticamente sus categorías, cuentas y métodos de pago.

## Automatización

El script `scripts/generar_recurrentes.sh` genera movimientos recurrentes y cuotas vencidas de forma idempotente. Puede ejecutarse desde `cron`; usa `flock` para impedir dos ejecuciones simultáneas y escribe el resultado en `logs/recurrentes.log`.

## Verificación

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```
