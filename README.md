# TaskBudget

Gestor web de finanzas personales construido con Django y PostgreSQL.

## Puesta en marcha

1. Crea la base PostgreSQL indicada por `POSTGRES_DB`.
2. Copia `.env.example` a `.env` y reemplaza los valores de ejemplo.
3. Django carga automáticamente el archivo `.env` al iniciar. Las variables
   definidas explícitamente por el sistema o el servicio tienen prioridad.
4. Ejecuta las migraciones y arranca el servidor:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

La pantalla `/registro/` crea el primer usuario y prepara automáticamente sus categorías, cuentas y métodos de pago.

## Experiencia móvil y captura de datos

- La aplicación incluye manifiesto PWA, navegación inferior móvil y una pantalla segura sin conexión. En producción debe servirse por HTTPS para poder instalarla desde el navegador.
- La actividad financiera reúne ingresos, gastos, transferencias y pagos de deuda, con filtros por texto, tipo y fecha.
- Los estados bancarios se importan desde CSV mediante una previsualización. Se aceptan columnas `fecha`, `concepto`/`descripcion` y `monto`, o columnas separadas de `debito` y `credito`. Ninguna fila se registra antes de confirmarla.
- La captura de comprobantes acepta una foto, sugiere datos con el proveedor de IA configurado y siempre exige revisión y confirmación manual. Sin IA disponible, el mismo flujo funciona con ingreso manual.

## Automatización

El script `scripts/generar_recurrentes.sh` genera movimientos recurrentes y cuotas vencidas de forma idempotente. Puede ejecutarse desde `cron`; usa `flock` para impedir dos ejecuciones simultáneas y escribe el resultado en `logs/recurrentes.log`.

El script `scripts/generar_recomendaciones_ia.sh` analiza diariamente los datos confirmados, objetivos y compromisos de cada usuario habilitado para IA. Guarda recomendaciones idempotentes, preguntas aclaratorias, evidencia, confianza y memoria de seguimiento en `logs/recomendaciones_ia.log`.

Ejemplo de cron, después de generar los movimientos recurrentes:

```cron
0 1 * * * PYTHON=/ruta/al/entorno/bin/python /bin/bash /ruta/al/proyecto/scripts/generar_recomendaciones_ia.sh
```

## Verificación

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```
