# Modelo de datos

TaskBudget utiliza el usuario estándar de Django y distribuye sus tablas de negocio en esquemas PostgreSQL. Esta guía resume el modelo actual; las migraciones son la fuente definitiva de la estructura física.

## 1. Relaciones principales

```text
Usuario
├── PerfilUsuario
├── CuentaFinanciera ── AjusteSaldo
├── Categoria ──┬── Subcategorías
│               ├── MovimientoFinanciero
│               ├── PresupuestoMensual
│               └── Tarea
├── MetodoPago ── MovimientoFinanciero
├── Etiqueta >──< MovimientoFinanciero
├── Acreedor ── Deuda ──< PagoDeuda
├── MovimientoRecurrente ──< MovimientoFinanciero
├── ImportacionBancaria ──< LineaImportacionBancaria ── MovimientoFinanciero
├── CapturaComprobante ── BorradorMovimientoIA ── MovimientoFinanciero
├── ObjetivoFinanciero
├── RecomendacionFinanciera
└── PerfilComportamientoFinanciero
```

`>──<` representa muchos a muchos y `──<` uno a muchos.

## 2. Usuarios y configuración

| Entidad | Propósito | Reglas destacadas |
| --- | --- | --- |
| `User` | Identidad, contraseña y roles Django. | `is_active`, `is_staff` e `is_superuser` controlan acceso. |
| `PerfilUsuario` | Imagen, teléfono y permiso de IA. | Uno por usuario. |
| `ConfiguracionIA` | Proveedor global, modelo, URL, timeout y clave cifrada. | Solo una fila mediante campo único. |
| `ConsumoIA` | Métricas por interacción. | Puede conservarse aunque el usuario sea eliminado. |
| `Notificacion` | Alertas y avisos. | La clave, cuando existe, es única por usuario. |

## 3. Catálogos personales

| Entidad | Campos principales | Integridad |
| --- | --- | --- |
| `Categoria` | usuario, padre, nombre, tipo, color | Nombre único por usuario/tipo en raíz y por padre en subcategorías. |
| `CuentaFinanciera` | nombre, tipo, saldo inicial, activa | Nombre único por usuario. |
| `MetodoPago` | nombre, tipo, activo | Nombre único por usuario. |
| `Etiqueta` | nombre, color | Nombre único por usuario. |
| `Acreedor` | nombre, teléfono, email, activo | Nombre único por usuario. |

Los tipos de cuenta son banco, efectivo, tarjeta, ahorro y otro. Los métodos son efectivo, transferencia, débito, crédito y otro.

## 4. Finanzas

### MovimientoFinanciero

Representa un ingreso o gasto. Puede vincular categoría, cuenta, método, acreedor de crédito, recurrencia, etiquetas y comprobante.

Estados:

- `pendiente`;
- `confirmado`;
- `eliminado`.

Campos de crédito:

- `fecha_pago`: primera fecha prevista de pago;
- `numero_cuotas_credito`;
- `acreedor_credito`.

Una recurrencia solo puede producir un movimiento por usuario y fecha.

### TransferenciaCuenta

Relaciona cuenta de origen y destino, monto y fecha. Una restricción impide usar la misma cuenta en ambos lados.

### AjusteSaldo

Conserva cuenta, movimiento de ajuste, saldo anterior, saldo nuevo, diferencia y motivo. Las relaciones protegidas evitan perder el contexto financiero por eliminaciones accidentales.

### PresupuestoMensual

Es único por usuario, categoría, año y mes.

### MovimientoRecurrente

Plantilla mensual con día del mes, monto, tipo, catálogos relacionados, estado activo y opción de aplicación automática.

## 5. Deudas

### Deuda

Puede originarse manualmente o desde un `MovimientoFinanciero` a crédito. Guarda monto inicial, saldo actual, acreedor, categoría, tasa anual opcional, pago mínimo, cantidad de cuotas y fechas.

Estados:

- `activa`;
- `pagada`;
- `cancelada`.

La relación `movimiento_origen` es uno a uno: un gasto genera como máximo una deuda.

### PagoDeuda

Representa un pago manual o una cuota numerada. Puede estar pendiente o confirmado. Una cuota numerada es única dentro de su deuda. Al confirmarse se asigna una cuenta y se registra la hora de confirmación.

Reglas de negocio relevantes:

- el pago no supera el saldo actual;
- confirmar reduce el saldo de la deuda;
- llegar a cero marca la deuda como pagada;
- eliminar un pago confirmado restaura el saldo hasta el monto inicial;
- las cuotas pendientes se reconstruyen desde el saldo y las cuotas ya confirmadas.

## 6. Importaciones y comprobantes

| Entidad | Propósito |
| --- | --- |
| `ImportacionBancaria` | Lote CSV previsualizado, confirmado o cancelado. |
| `LineaImportacionBancaria` | Fila normalizada con huella de duplicado y movimiento resultante. |
| `CapturaComprobante` | Imagen, extracción, error y estado del flujo. |
| `BorradorMovimientoIA` | Movimiento temporal pendiente de confirmación. |

El borrador exige monto positivo y al menos una cuota. Puede estar pendiente, confirmado, cancelado o expirado. La migración `0041_borradormovimientoia_etiquetas` añade etiquetas al borrador para conservarlas durante la confirmación de comprobantes.

## 7. Análisis y recomendaciones

| Entidad | Propósito | Integridad |
| --- | --- | --- |
| `PerfilComportamientoFinanciero` | Resumen JSON de patrones y calidad de datos. | Uno por usuario; puede marcarse desactualizado. |
| `ObjetivoFinanciero` | Meta de ahorro, emergencia, deuda, límite de gasto u otra. | Montos no negativos y prioridad de 1 a 5. |
| `RecomendacionFinanciera` | Consejo, evidencia, acciones, confianza y feedback. | Código único por usuario y fecha; prioridad de 1 a 10. |

Los objetivos pueden estar activos, logrados, pausados o cancelados. Las recomendaciones pueden estar nuevas, aceptadas, descartadas o completadas.

## 8. Tareas

`Tarea` guarda título, descripción, categoría de tipo tarea, fecha, horario, estado y prioridad. Los estados son pendiente, trabajando, finalizada y cancelada; la prioridad puede ser baja, media o alta.

## 9. Auditoría

| Entidad | Propósito |
| --- | --- |
| `RegistroAuditoria` | Acción, modelo, identificador, representación, cambios, motivo, IP y fecha. |
| `EliminacionRegistro` | Rastro descriptivo de un objeto eliminado. |

Las acciones auditables definidas son crear, actualizar, eliminar, confirmar y ajustar saldo.

## 10. Política de eliminación

El modelo mezcla varias estrategias según el riesgo:

- `CASCADE` para datos estrictamente pertenecientes al usuario o al padre;
- `PROTECT` cuando borrar el objeto rompería trazabilidad financiera;
- `SET_NULL` para conservar movimientos o consumo aunque desaparezca un catálogo opcional.

Antes de cambiar una relación o política de eliminación, revisa los flujos de saldo, deuda, auditoría e importación y añade una migración con pruebas.
