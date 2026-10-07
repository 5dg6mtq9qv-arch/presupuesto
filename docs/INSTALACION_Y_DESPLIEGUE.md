# Instalación y despliegue

## 1. Requisitos

- Linux o un entorno compatible con Python.
- Python 3.11, que es la versión del entorno actual del proyecto.
- PostgreSQL accesible desde el servidor.
- `flock` si se usarán los scripts programados incluidos.
- Un servidor HTTPS para habilitar correctamente la instalación PWA en producción.

El repositorio no incluye Docker ni un servidor WSGI/ASGI de producción en `requirements.txt`. Deben incorporarse según la infraestructura elegida.

## 2. Instalación local

Desde la raíz del repositorio:

```bash
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

No agregues `.env` al control de versiones. Ya está ignorado por Git.

## 3. Crear PostgreSQL

Ejemplo ejecutado como administrador de PostgreSQL:

```sql
CREATE USER taskbudget_user WITH PASSWORD 'una-clave-larga';
CREATE DATABASE taskbudget OWNER taskbudget_user;
```

El propietario necesita permiso para crear esquemas. Las migraciones crean y usan, entre otros, `categorias`, `tareas`, `movimientos_financieros`, `finanzas`, `deudas`, `pagos_deudas`, `usuarios`, `analisis`, `configuracion` y `auditoria`.

Configura `.env`:

```dotenv
POSTGRES_DB=taskbudget
POSTGRES_USER=taskbudget_user
POSTGRES_PASSWORD=una-clave-larga
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

DJANGO_SECRET_KEY=reemplazar-por-un-secreto-largo-y-aleatorio
DJANGO_DEBUG=true
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_SESSION_COOKIE_SECURE=false
DJANGO_CSRF_COOKIE_SECURE=false
```

Django lee `.env` automáticamente. Una variable ya definida por el sistema o el servicio tiene prioridad sobre el archivo.

## 4. Inicializar la aplicación

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py check
.venv/bin/python manage.py runserver
```

Direcciones locales:

- aplicación: `http://127.0.0.1:8000/`;
- registro: `http://127.0.0.1:8000/registro/`;
- acceso: `http://127.0.0.1:8000/accounts/login/`;
- administración Django: `http://127.0.0.1:8000/admin/`.

El registro público crea usuarios normales, no administradores. `createsuperuser` crea la cuenta de administración inicial.

## 5. Variables de entorno

| Variable | Propósito | Valor de ejemplo |
| --- | --- | --- |
| `POSTGRES_DB` | Base de datos | `taskbudget` |
| `POSTGRES_USER` | Usuario PostgreSQL | `taskbudget_user` |
| `POSTGRES_PASSWORD` | Contraseña PostgreSQL | secreto |
| `POSTGRES_HOST` | Servidor PostgreSQL | `127.0.0.1` |
| `POSTGRES_PORT` | Puerto PostgreSQL | `5432` |
| `DJANGO_SECRET_KEY` | Firma de sesiones y raíz criptográfica por defecto | secreto largo |
| `DJANGO_DEBUG` | Modo de depuración | `false` en producción |
| `DJANGO_SECURE_SSL_REDIRECT` | Fuerza HTTPS | `true` en producción |
| `DJANGO_SESSION_COOKIE_SECURE` | Cookie de sesión solo HTTPS | `true` en producción |
| `DJANGO_CSRF_COOKIE_SECURE` | Cookie CSRF solo HTTPS | `true` en producción |
| `STATIC_ROOT` | Destino de `collectstatic` | `/var/www/presupuesto/staticfiles` |
| `MEDIA_ROOT` | Imágenes y comprobantes | `<proyecto>/media` |
| `AI_*` | Configuración inicial de IA | consulta la guía de IA |

### Correo transaccional y recuperación de contraseña

El registro público crea una cuenta inactiva y envía una solicitud al administrador; el usuario solo puede iniciar sesión después de ser aprobado. El acceso continúa usando nombre de usuario y contraseña. Para enviar estas notificaciones y los enlaces de **Olvidé mi contraseña** en producción, el despliegue actual usa el buzón `contacto@felixiot.site` de Hostinger Mail:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.hostinger.com
EMAIL_PORT=465
EMAIL_HOST_USER=contacto@felixiot.site
EMAIL_HOST_PASSWORD=contraseña-del-buzón
EMAIL_USE_TLS=false
EMAIL_USE_SSL=true
EMAIL_TIMEOUT=15
DEFAULT_FROM_EMAIL=Félix IoT <contacto@felixiot.site>
EMAIL_CONFIG_ENCRYPTION_KEY=una-clave-maestra-independiente-y-estable
PASSWORD_RESET_TIMEOUT=3600
REGISTRATION_APPROVAL_EMAIL=contacto@felixiot.site
REGISTRATION_DECISION_TIMEOUT=86400
```

Usa exactamente el remitente autorizado por Hostinger. `EMAIL_USE_TLS` y `EMAIL_USE_SSL` no deben estar activos al mismo tiempo. Si el puerto 465 no estuviera disponible en otra infraestructura, Hostinger admite como alternativa el puerto 587 con TLS (`EMAIL_USE_TLS=true` y `EMAIL_USE_SSL=false`). En desarrollo, si no se define `EMAIL_BACKEND`, Django imprime los mensajes en la consola y no envía correo real.

Un administrador puede configurar el correo desde la propia plataforma en **Administración → Configurar correo**, sin entrar a Django Admin. Los usuarios normales no ven el enlace ni pueden acceder directamente a la página. La contraseña SMTP se cifra antes de guardarse y nunca vuelve a mostrarse. La configuración activa del sistema tiene prioridad sobre las variables SMTP anteriores, que permanecen como respaldo. Para verificarla:

En esa misma pantalla se configura el destinatario de las nuevas solicitudes de acceso. Cada registro envía al administrador un enlace firmado para revisar la solicitud y elegir entre aprobar o rechazar. El enlace requiere autenticación administrativa y vence según `REGISTRATION_DECISION_TIMEOUT`. Al aprobar, la cuenta y el acceso al asistente de IA se habilitan automáticamente.

1. Completa el servidor, puerto, usuario, remitente y contraseña.
2. Activa únicamente SSL o TLS según el puerto.
3. Escribe un destinatario de prueba.
4. Pulsa **Guardar y enviar prueba**.

La clave `EMAIL_CONFIG_ENCRYPTION_KEY` debe conservarse estable entre despliegues. Si se omite, el cifrado deriva de `DJANGO_SECRET_KEY`, que tampoco debe cambiar o la contraseña almacenada dejará de poder descifrarse.

`STATIC_ROOT` y `MEDIA_ROOT` son opcionales en `.env`; si no se indican se usan los valores definidos en `settings.py`.

## 6. Preparación para producción

### Ajustes obligatorios

1. Usa valores secretos únicos y deja `DJANGO_DEBUG=false`.
2. Activa las tres opciones seguras de HTTPS.
3. Revisa `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` en `taskbudget/settings.py`. Actualmente contienen el dominio de despliegue existente, por lo que un dominio nuevo requiere modificar esos valores.
4. Conserva `SECURE_PROXY_SSL_HEADER` y configura el proxy para enviar `X-Forwarded-Proto: https`.
5. Usa un servidor WSGI o ASGI mantenido, ejecutado como usuario sin privilegios. Instálalo y fíjalo como dependencia de la implementación.
6. Sirve `/static/` y `/media/` desde el proxy o almacenamiento elegido. En producción Django no publica automáticamente los archivos multimedia.
7. Restringe el acceso a PostgreSQL y realiza respaldos regulares.
8. Protege `.env`, `MEDIA_ROOT`, los respaldos y los logs con permisos del sistema operativo.

### Archivos estáticos

```bash
.venv/bin/python manage.py collectstatic --noinput
```

El usuario del despliegue debe poder escribir en `STATIC_ROOT` durante la actualización. El servidor web solo necesita leer el resultado.

### Comprobaciones

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py check --deploy
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```

Revisa los avisos de `check --deploy` según el proxy y la infraestructura utilizados.

## 7. Secuencia de actualización

Una actualización conservadora sigue este orden:

1. Realizar respaldo de PostgreSQL y, si aplica, de `MEDIA_ROOT`.
2. Obtener la versión nueva del código.
3. Instalar dependencias fijadas.
4. Ejecutar migraciones.
5. Recolectar archivos estáticos.
6. Ejecutar `check` y las pruebas relevantes.
7. Reiniciar el servicio de aplicación.
8. Verificar inicio de sesión, dashboard, archivos estáticos y logs.

Comandos centrales:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check
```

## 8. PWA y Android

La aplicación publica dinámicamente:

- `/manifest.webmanifest`;
- `/service-worker.js`;
- `/offline/`.

Para que el navegador permita instalarla fuera de `localhost`, el sitio debe servirse por HTTPS y el manifiesto, icono y service worker deben responder sin errores. El service worker evita almacenar páginas financieras privadas y ofrece una pantalla de desconexión; no convierte la aplicación en un sistema financiero sin conexión.

## 9. Aspectos que la infraestructura debe decidir

Este repositorio no impone una tecnología específica para:

- servidor WSGI/ASGI;
- proxy inverso;
- servicio del sistema o contenedor;
- correo transaccional;
- almacenamiento externo de archivos;
- observabilidad y rotación de logs.

Documenta esas decisiones en el inventario del entorno donde se despliegue TaskBudget.
