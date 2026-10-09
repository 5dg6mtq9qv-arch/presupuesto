# Arquitectura

## 1. Visión general

Finanzas Claras es una aplicación monolítica Django renderizada en servidor. No existe una API REST pública: el navegador interactúa con vistas Django, formularios HTML, sesiones y protección CSRF.

```text
Navegador / PWA
      |
      | HTTPS, formularios y sesiones
      v
Django: URLs -> vistas -> formularios -> servicios
      |                         |
      |                         +-> proveedor de IA compatible
      v
PostgreSQL por esquemas     MEDIA_ROOT / STATIC_ROOT
```

## 2. Capas del proyecto

| Capa | Archivos principales | Responsabilidad |
| --- | --- | --- |
| Configuración | `taskbudget/settings.py`, `taskbudget/urls.py` | Entorno, base, seguridad, aplicaciones y rutas raíz. |
| Enrutamiento | `core/urls.py` | Rutas funcionales de la aplicación. |
| Presentación | `core/views.py`, `core/templates/`, `core/static/` | Pantallas, filtros, respuestas, PWA y exportaciones. |
| Validación | `core/forms.py` | Formularios, opciones limitadas por usuario y validaciones. |
| Dominio | `core/models.py` | Entidades, relaciones, estados y restricciones. |
| Servicios | `core/services.py` | Saldos, deudas a crédito, cuotas, recurrentes, alertas y datos iniciales. |
| IA | `core/ai_assistant.py`, `core/autonomous_finance.py`, `core/financial_profile.py` | Herramientas, contexto, borradores, perfiles y recomendaciones. |
| Integraciones | `core/bank_import.py`, `core/receipt_capture.py` | CSV bancario y lectura de comprobantes. |
| Operación | `core/management/commands/`, `scripts/` | Trabajos programados y mantenimiento. |
| Verificación | `core/tests.py`, `core/test_*.py` | Pruebas de dominio, producto e IA. |

## 3. Aislamiento por usuario

Las entidades funcionales incluyen un usuario directo o se alcanzan mediante una relación propiedad del usuario. Las vistas filtran por `request.user`, y los formularios limitan categorías, cuentas, métodos, etiquetas, acreedores y deudas al propietario autenticado.

Esta separación también se aplica a herramientas del asistente. No se aceptan identificadores arbitrarios como sustituto de la propiedad del registro.

## 4. Flujos financieros principales

### Movimiento normal

```text
Formulario -> validación -> MovimientoFinanciero
                         -> auditoría
                         -> recálculo de saldo al consultar
```

Los saldos no se mantienen como un contador mutable. `saldo_actual_cuenta` los deriva desde saldo inicial, movimientos confirmados, pagos de deuda y transferencias. Esto reduce desincronizaciones entre módulos.

### Compra a crédito

```text
Gasto confirmado + método Crédito
            |
            v
sincronizar_deuda_compra_credito
            |
            +-> Deuda vinculada al movimiento
            +-> N cuotas pendientes mensuales

Confirmación de cuota -> reduce saldo de cuenta y saldo de deuda
```

El gasto registra el consumo en su fecha. La cuenta no disminuye hasta el pago de la deuda, evitando contar dos veces la salida de caja.

### Captura de comprobante

```text
Imagen -> sugerencia IA o formulario vacío
       -> revisión humana
       -> BorradorMovimientoIA (30 minutos)
       -> confirmación explícita
       -> movimiento + comprobante
       -> deuda y cuotas si el método es Crédito
```

### Importación bancaria

```text
CSV -> análisis y normalización -> ImportacionBancaria previsualizada
    -> huellas de duplicado -> selección humana
    -> movimientos confirmados por fila seleccionada
```

### Recurrentes y cuotas

Los servicios generan ocurrencias vencidas hasta una fecha determinada. Las restricciones de unicidad y las búsquedas previas hacen idempotentes las ejecuciones repetidas.

## 5. Análisis financiero

El análisis utiliza movimientos confirmados y pagos registrados. Admite rangos semanales, mensuales, semestrales, anuales, históricos o personalizados.

- La vista de caja observa entradas y salidas efectivas.
- La vista de consumo permite atribuir compras a la fecha en que se originaron.
- La proyección usa únicamente saldos, recurrentes activos y cuotas pendientes conocidas.
- Los presupuestos se comparan con gastos confirmados por categoría y subcategoría.

El perfil de comportamiento financiero se guarda como JSON calculado. Las señales lo marcan como desactualizado cuando cambian movimientos, recurrentes o categorías.

## 6. Subsistema de IA

La configuración se resuelve desde base de datos o entorno. La comunicación usa endpoints compatibles con OpenAI y registra métricas de uso.

El asistente dispone de herramientas internas de solo lectura y de escritura controlada. Para ingresos y gastos rápidos:

1. interpreta los campos;
2. resuelve catálogos del usuario;
3. crea un borrador con vencimiento;
4. presenta el resumen;
5. exige confirmación explícita;
6. crea el registro dentro de una transacción.

Las recomendaciones proactivas combinan reglas deterministas, perfil histórico, objetivos y memoria de feedback. Las respuestas se validan antes de mostrarse.

## 7. Seguridad

- Autenticación y sesiones de Django.
- Protección CSRF en formularios.
- Rutas funcionales protegidas con `login_required`.
- Rutas administrativas de la aplicación protegidas por `is_staff`.
- Restricción de registros por propietario.
- Transacciones y bloqueos `select_for_update` en confirmaciones sensibles.
- Validadores de contraseña de Django.
- Claves de IA cifradas en base.
- Cookies seguras y redirección HTTPS configurables por entorno.
- Motivos y cambios relevantes conservados en auditoría.

El cifrado de claves de IA no sustituye la protección de `.env`, la base de datos, los respaldos o el servidor.

## 8. Auditoría

`RegistroAuditoria` almacena creación, actualización, eliminación, confirmación y ajuste de saldo con usuario, objeto, cambios, motivo e IP cuando está disponible. `EliminacionRegistro` conserva una referencia legible de elementos eliminados.

No es un libro contable inmutable: administradores de infraestructura con acceso a la base pueden modificar los registros. Si se requiere cumplimiento regulatorio, deben añadirse controles de inmutabilidad, retención y revisión acordes a la normativa aplicable.

## 9. PWA

El manifiesto y service worker se generan mediante vistas. El service worker almacena recursos de interfaz y muestra una página segura cuando no hay conexión. No almacena páginas autenticadas ni datos financieros privados para uso offline.

## 10. Extensión del sistema

Al agregar una función:

1. modela la entidad y crea una migración;
2. limita consultas y opciones al usuario propietario;
3. ubica reglas financieras en servicios, no solo en la vista;
4. usa transacciones para operaciones de varios registros;
5. registra auditoría si cambia dinero o permisos;
6. añade pruebas de autorización, validación e idempotencia;
7. actualiza la guía de usuario, el modelo de datos y la operación cuando corresponda.
