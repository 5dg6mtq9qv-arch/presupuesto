# Operación y mantenimiento

## 1. Comandos periódicos

### Generar recurrentes y cuotas

```bash
.venv/bin/python manage.py generar_recurrentes
```

Opciones:

```bash
.venv/bin/python manage.py generar_recurrentes --hasta 2026-10-31
.venv/bin/python manage.py generar_recurrentes --usuario nombre_usuario
```

El comando genera movimientos recurrentes y pagos de deuda vencidos hasta hoy, o hasta la fecha indicada. Informa creados y duplicados omitidos.

### Generar recomendaciones

```bash
.venv/bin/python manage.py generar_recomendaciones_ia
.venv/bin/python manage.py generar_recomendaciones_ia --usuario nombre_usuario
```

Solo procesa usuarios activos autorizados para IA y superusuarios.

### Recalcular perfiles financieros

```bash
.venv/bin/python manage.py recalcular_perfiles_financieros
.venv/bin/python manage.py recalcular_perfiles_financieros --usuario nombre_usuario
```

Úsalo después de cambios masivos de datos o reglas de análisis. En el uso normal, las señales invalidan el perfil y se recalcula cuando corresponde.

## 2. Programación con cron

Los scripts incluidos cargan `.env`, usan `flock` para impedir solapamientos y escriben logs.

Ejemplo:

```cron
0 1 * * * PYTHON=/ruta/TaskBudget/.venv/bin/python /bin/bash /ruta/TaskBudget/scripts/generar_recurrentes.sh
15 1 * * * PYTHON=/ruta/TaskBudget/.venv/bin/python /bin/bash /ruta/TaskBudget/scripts/generar_recomendaciones_ia.sh
```

Genera primero las finanzas automáticas para que las recomendaciones trabajen con las obligaciones más recientes.

Variables admitidas por los scripts:

- `PYTHON`: intérprete del entorno;
- `LOG_DIR`: directorio de logs;
- `LOCK_FILE`: archivo de bloqueo.

Logs predeterminados:

- `logs/recurrentes.log`;
- `logs/recomendaciones_ia.log`.

Configura rotación externa: los scripts agregan contenido y no eliminan logs antiguos.

## 3. Verificación de calidad

Antes de desplegar:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```

Para aislar áreas:

```bash
.venv/bin/python manage.py test core.tests
.venv/bin/python manage.py test core.test_product_features
.venv/bin/python manage.py test core.test_quick_ai_movements
.venv/bin/python manage.py test core.test_autonomous_finance
.venv/bin/python manage.py test core.test_ai_usage
```

Las pruebas cubren cálculos financieros, permisos, flujos de producto, borradores y consumo de IA. Un cambio de reglas de saldo, crédito o deuda debe añadir casos de regresión específicos.

## 4. Respaldo

Un respaldo completo incluye:

1. PostgreSQL;
2. `MEDIA_ROOT`, que contiene perfiles, comprobantes y capturas;
3. `.env` o sus secretos almacenados de forma segura;
4. configuración del servicio, proxy y cron.

Ejemplo de base:

```bash
pg_dump --format=custom --file=taskbudget.dump --dbname=taskbudget
```

No guardes el respaldo dentro de una carpeta pública. Cífralo, controla acceso, conserva copias fuera del servidor y prueba restauraciones periódicas.

## 5. Restauración de referencia

En un entorno vacío y compatible:

```bash
createdb taskbudget_restaurada
pg_restore --clean --if-exists --no-owner --dbname=taskbudget_restaurada taskbudget.dump
```

Después:

1. restaura `MEDIA_ROOT` manteniendo rutas y permisos;
2. configura variables del entorno;
3. ejecuta `manage.py migrate` para aplicar migraciones posteriores al respaldo;
4. ejecuta `manage.py check`;
5. valida usuarios, cuentas, saldos, deudas y acceso a archivos.

Adapta usuario, host y autenticación a tu instalación de PostgreSQL. Prueba primero fuera de producción.

## 6. Monitoreo mínimo

Supervisa:

- disponibilidad HTTP y certificado TLS;
- errores del servidor de aplicación y del proxy;
- espacio de PostgreSQL, `MEDIA_ROOT`, estáticos y logs;
- estado y antigüedad de los respaldos;
- salida de los cron y presencia de bloqueos antiguos;
- tasa de errores, latencia, tokens y costo estimado de IA;
- crecimiento de importaciones, capturas y auditoría.

Una ejecución que no crea registros puede ser correcta por idempotencia. Investiga cuando exista un traceback, falle el comando o dejen de aparecer obligaciones esperadas.

## 7. Diagnóstico frecuente

### PostgreSQL informa que no puede crear un esquema

Ejecuta las migraciones con el propietario de la base o concede permiso de creación sobre la base. No cambies los `db_table` a tablas públicas como arreglo rápido.

### Los estilos no aparecen en producción

- ejecuta `collectstatic`;
- confirma `STATIC_ROOT`;
- verifica que el proxy publique `/static/`;
- revisa permisos y caché del navegador.

### Las imágenes no aparecen

- confirma que el archivo exista bajo `MEDIA_ROOT`;
- configura el proxy para `/media/`;
- revisa permisos del usuario que ejecuta Django y del servidor web.

### Hay un bucle de redirección HTTPS

Confirma que el proxy envíe `X-Forwarded-Proto: https` y que solo exista una política coherente de redirección. El proyecto confía en ese encabezado mediante `SECURE_PROXY_SSL_HEADER`.

### No se generan recurrentes

- revisa que estén activos y su fecha sea aplicable;
- confirma la ruta de `PYTHON` en cron;
- inspecciona `logs/recurrentes.log`;
- ejecuta el comando manualmente con `--usuario`;
- comprueba que otra ejecución no mantenga el bloqueo.

### Las cuotas no coinciden

Verifica monto inicial, saldo actual, número de cuotas, fecha de primera cuota y pagos ya confirmados. Editar fechas reprograma cuotas numeradas; los pagos confirmados representan historia y deben revisarse antes de una corrección masiva.

### La PWA no ofrece instalación

- usa HTTPS o `localhost`;
- comprueba manifiesto, icono y service worker en las herramientas del navegador;
- limpia versiones antiguas del service worker si cambió el dominio o alcance.

## 8. Mantenimiento de migraciones

Después de modificar modelos:

```bash
.venv/bin/python manage.py makemigrations core
.venv/bin/python manage.py migrate
.venv/bin/python manage.py makemigrations --check --dry-run
```

Revisa el archivo generado antes de aplicarlo, especialmente si cambia esquemas, relaciones protegidas, campos monetarios o restricciones únicas. Nunca borres migraciones ya aplicadas en producción para “arreglar” el historial.

## 9. Lista de verificación de una incidencia

1. Registrar hora, usuario afectado, ruta y acción exacta.
2. Reproducir de forma segura sin modificar datos ajenos.
3. Revisar logs del proxy, aplicación, cron y proveedor de IA.
4. Confirmar versión de código y migraciones aplicadas.
5. Consultar auditoría y estados relacionados.
6. Respaldar antes de corregir datos manualmente.
7. Añadir una prueba que reproduzca el fallo.
8. Documentar la corrección y validar saldos derivados.
