# Configuración de IA

La IA es opcional. El resto de TaskBudget, incluida la captura manual de comprobantes, funciona sin un proveedor configurado.

## 1. Funciones que utilizan IA

- conversación financiera basada en los datos del usuario;
- consulta de movimientos, saldos, deudas, pagos, presupuestos y catálogos;
- análisis avanzado y simulación de escenarios;
- objetivos y recomendaciones proactivas;
- preparación de movimientos en lenguaje natural;
- extracción de datos visibles desde imágenes de comprobantes.

La IA no guarda movimientos directamente sin el flujo de confirmación correspondiente. En captura de comprobantes, los datos extraídos siempre son sugerencias sujetas a revisión.

## 2. Dos formas de configurar

### Variables de entorno

Se usan cuando todavía no existe una fila de `ConfiguracionIA` en la base:

```dotenv
AI_ASSISTANT_ENABLED=true
AI_PROVIDER=groq
AI_API_KEY=
AI_MODEL=openai/gpt-oss-20b
AI_BASE_URL=https://api.groq.com/openai/v1
AI_TIMEOUT_SECONDS=25
AI_CONFIG_ENCRYPTION_KEY=
```

### Pantalla administrativa

Un usuario staff puede abrir **Configuración de IA**, seleccionar proveedor y modelo, guardar la clave y probar la conexión. Se admiten OpenAI, Google Gemini, Groq y un servicio personalizado compatible con el formato OpenAI.

Importante: desde que existe un registro de configuración en la base, ese registro tiene prioridad completa sobre las variables `AI_*`, incluso si está desactivado. Para cambiar el comportamiento usa la pantalla administrativa o administra explícitamente ese registro.

## 3. Claves y cifrado

Las claves guardadas desde la interfaz se cifran con Fernet antes de persistirse. La clave criptográfica se deriva de:

1. `AI_CONFIG_ENCRYPTION_KEY`, si está definida;
2. en caso contrario, `DJANGO_SECRET_KEY`.

En producción conviene definir una `AI_CONFIG_ENCRYPTION_KEY` independiente y estable. Si cambias la raíz utilizada después de guardar una clave, la clave existente ya no podrá descifrarse y deberás introducirla otra vez.

El sistema conserva además únicamente el sufijo de la clave para identificarla visualmente. Nunca coloques claves reales en `.env.example`, documentación, commits o logs.

## 4. Control de acceso

Para usar IA deben cumplirse las condiciones siguientes:

- proveedor activo y correctamente configurado;
- usuario autenticado;
- usuario autorizado mediante `PerfilUsuario.puede_usar_asistente_ia`, o usuario superadministrador.

La autorización individual se gestiona al crear o editar usuarios. El acceso de un superusuario permanece habilitado.

## 5. Datos enviados al proveedor

Según la operación, el proveedor puede recibir:

- la pregunta y hasta diez mensajes recientes de la conversación;
- contexto financiero calculado para el usuario autenticado;
- resultados limitados de herramientas internas;
- patrones del perfil financiero, objetivos y memoria de recomendaciones;
- una versión redimensionada y recomprimida de la imagen de un comprobante.

El sistema instruye al modelo para no acceder a otros usuarios, no inventar datos y no revelar secretos. Aun así, el operador debe revisar las condiciones de tratamiento de datos del proveedor elegido y obtener los consentimientos que correspondan.

No uses el asistente para almacenar contraseñas, claves, números completos de tarjetas u otra información innecesaria.

## 6. Consumo y costo

Cada interacción registra, cuando el proveedor los informa:

- proveedor y modelo;
- operación;
- tokens de entrada, salida, caché y razonamiento;
- duración, estado HTTP y código de error;
- identificador de petición del proveedor;
- usuario e identificador de interacción.

El panel administrativo presenta un resumen general y una tabla consolidada de los usuarios que utilizaron la IA. Permite filtrar por fechas o usuario y muestra interacciones, tokens, costo estimado, último uso de IA y último acceso al sistema. No muestra el detalle de cada solicitud. La cifra de costo es orientativa: la tarifa real depende del proveedor, plan, impuestos, descuentos y servicios adicionales.

## 7. Recomendaciones programadas

Ejecución manual para todos los usuarios autorizados:

```bash
.venv/bin/python manage.py generar_recomendaciones_ia
```

Para uno solo:

```bash
.venv/bin/python manage.py generar_recomendaciones_ia --usuario nombre_usuario
```

El script `scripts/generar_recomendaciones_ia.sh` carga `.env`, impide ejecuciones simultáneas con `flock` y escribe en `logs/recomendaciones_ia.log`.

Ejemplo diario, después de generar recurrentes:

```cron
15 1 * * * PYTHON=/ruta/TaskBudget/.venv/bin/python /bin/bash /ruta/TaskBudget/scripts/generar_recomendaciones_ia.sh
```

## 8. Diagnóstico

### “El asistente todavía no está configurado”

- Confirma que la configuración activa tenga clave, modelo y URL.
- Si ya existe configuración en base, recuerda que las variables de entorno no la reemplazan.
- Prueba la conexión desde la pantalla administrativa.

### “No tienes autorización”

- Verifica el permiso individual del perfil.
- Confirma que el usuario esté activo.

### La captura queda en revisión manual

- Comprueba que el modelo acepte entrada de imágenes.
- Revisa el formato y legibilidad de la imagen.
- Consulta el error mostrado y el panel de consumo.
- Completa manualmente los campos; la captura no depende obligatoriamente de IA.

### Una clave guardada dejó de funcionar tras cambiar secretos

Restaura la misma `AI_CONFIG_ENCRYPTION_KEY` o vuelve a introducir la clave del proveedor. Cambiar `DJANGO_SECRET_KEY` también afecta el descifrado si no se configuró una clave maestra independiente.
