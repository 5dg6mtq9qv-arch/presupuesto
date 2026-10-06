# Guía de usuario

Esta guía describe el uso funcional de TaskBudget. Todas las operaciones financieras se limitan al usuario que inició sesión.

## 1. Primer ingreso

Puedes crear una cuenta desde `/registro/` o pedir a un administrador que la cree. Al entrar, el sistema prepara automáticamente:

- las cuentas `General` y `Efectivo`;
- los métodos `Efectivo`, `Transferencia`, `Débito` y `Crédito`;
- categorías y subcategorías financieras iniciales.

El dashboard muestra pasos de configuración, saldos, resumen del periodo y movimientos recientes. Conviene completar primero las cuentas reales, categorías y métodos de pago.

Durante el primer ingreso aparece un recorrido breve de tres pantallas. Explica cómo se calculan los saldos, la diferencia entre un gasto normal y una compra a crédito, y propone una ruta inicial. Al terminar conduce a **Cuentas**. El recorrido se muestra una sola vez; la lista **Primeros pasos** permanece en el dashboard hasta completar la configuración de cuentas, un ingreso y un gasto.

## 2. Conceptos esenciales

- **Cuenta:** lugar donde se mantiene dinero, por ejemplo banco, efectivo o ahorro.
- **Método de pago:** forma utilizada para pagar. El tipo `Crédito` tiene un comportamiento especial.
- **Movimiento:** ingreso o gasto. Solo un movimiento confirmado afecta los cálculos.
- **Categoría y subcategoría:** clasificación jerárquica para análisis y presupuestos.
- **Etiqueta:** clasificación adicional y reutilizable.
- **Deuda:** obligación con saldo, acreedor, fechas y cuotas.
- **Pago de deuda:** salida de dinero asociada a una deuda y, al confirmarse, a una cuenta.

## 3. Cuentas y saldos

En **Cuentas** puedes crear y desactivar cuentas, consultar el saldo calculado, transferir dinero o ajustar el saldo.

El saldo se calcula así:

```text
saldo inicial
+ ingresos confirmados
- gastos confirmados que no sean a crédito
- pagos de deuda confirmados
+ transferencias recibidas
- transferencias enviadas
```

Un gasto realizado con un método de tipo `Crédito` no reduce inmediatamente la cuenta: crea una deuda. La salida de caja ocurre cuando se confirma el pago de una cuota.

### Ajustar saldo

El ajuste pide el nuevo saldo y un motivo. El sistema conserva el saldo anterior, la diferencia y el movimiento asociado para auditoría. Usa esta opción para conciliaciones, no para ocultar ingresos o gastos.

### Transferir

Selecciona dos cuentas distintas, monto, fecha y nota opcional. La transferencia no se considera ingreso ni gasto: disminuye la cuenta de origen y aumenta la de destino.

## 4. Ingresos y gastos

En **Ingresos y gastos** puedes buscar y filtrar por estado, categoría, cuenta, método y etiqueta.

Para registrar un movimiento normal:

1. Elige ingreso o gasto.
2. Completa concepto, monto y fecha.
3. Selecciona categoría, cuenta y método cuando correspondan.
4. Añade etiquetas, comprobante o nota si son útiles.
5. Guarda o confirma el movimiento según el flujo mostrado.

### Gasto a crédito

Cuando el movimiento es un gasto y el método seleccionado tiene tipo `Crédito`, aparecen los datos adicionales:

- acreedor;
- fecha del primer pago;
- número de cuotas.

Al confirmar, el sistema crea o sincroniza una deuda por el importe del gasto y distribuye el monto entre las cuotas mensuales. La última cuota absorbe cualquier diferencia de redondeo.

Si el gasto cambia antes de haber pagos confirmados, también se actualizan la deuda y sus cuotas. Si ya existen pagos confirmados, evita cambiar sus condiciones sin revisar el plan resultante.

## 5. Deudas y pagos

Una deuda puede crearse manualmente o generarse desde un gasto a crédito. Al crearla manualmente solo debes indicar las cuotas pendientes de pago, el valor de cada cuota y la fecha del próximo pago. El sistema calcula el total pendiente y genera esas cuotas mensuales; no reconstruye pagos anteriores.

### Filtros del listado

El listado admite:

- texto, estado y categoría;
- **Fecha de pago desde/hasta:** busca deudas que tengan cuotas o pagos dentro del rango;
- **Fecha generada desde/hasta:** filtra por la fecha de inicio de la deuda.

Todos los campos de fecha pueden quedar vacíos; en ese caso no limitan el listado.

### Registrar y confirmar pagos

- Un pago manual se registra ya confirmado y reduce el saldo de la deuda.
- Una cuota programada permanece pendiente hasta que eliges una cuenta y la confirmas.
- El sistema impide confirmar una cuota si la cuenta no tiene saldo suficiente.
- Cuando el saldo llega a cero, la deuda pasa a `Pagada`.
- Si eliminas un pago confirmado, el monto vuelve al saldo de la deuda y esta puede reactivarse.

La pantalla de deudas genera y muestra el plan pendiente, incluido el próximo mes. Las alertas avisan de cuotas próximas o vencidas.

### Importar una tabla de amortización

Desde el asistente financiero puedes adjuntar una tabla de amortización en PDF. El sistema lee el número de cuotas pagadas, las cuotas totales y cada fila pendiente con su fecha e importe. Antes de guardar muestra un resumen con el total pendiente, el próximo pago, la última cuota y cualquier fecha corregida por una inconsistencia evidente del documento.

La importación solo se realiza al elegir **Generar cuotas pendientes**. Las cuotas anteriores se registran como avance previo, pero no se crean pagos históricos. Los nombres e identificaciones personales impresos en el PDF no se conservan.

## 6. Presupuestos

Los presupuestos se definen por categoría, mes y año. El sistema compara el límite con los gastos confirmados y puede:

- mostrar uso, disponible y proyección;
- copiar los presupuestos del mes anterior;
- sugerir importes a partir del historial;
- notificar cuando el uso alcanza el 80 % o supera el 100 %.

Si asignas un presupuesto a una categoría principal, el análisis puede incluir sus subcategorías. Evita crear límites solapados si no quieres contar la misma familia desde dos perspectivas.

## 7. Movimientos recurrentes

Los recurrentes representan ingresos o gastos mensuales. Define concepto, monto, día del mes, categoría, cuenta, método y si debe aplicarse automáticamente.

- Si el mes tiene menos días, se usa el último día disponible.
- La generación es idempotente: una misma recurrencia no crea dos movimientos para la misma fecha.
- Los registros automáticos se producen con el comando o script descrito en la guía de operación.

## 8. Importación bancaria CSV

Desde **Actividad** puedes cargar un estado bancario. El archivo debe pesar hasta 2 MB y contener como máximo 1.000 movimientos válidos.

Columnas reconocidas:

- `fecha`;
- `concepto` o `descripcion`;
- `monto`; o, como alternativa, columnas separadas `debito` y `credito`.

Se admiten separadores coma, punto y coma, tabulación o barra vertical, y varios formatos habituales de fecha. El flujo es:

1. Selecciona la cuenta y el CSV.
2. Revisa la previsualización.
3. Excluye duplicados o filas no deseadas.
4. Confirma las filas seleccionadas.
5. Clasifica los registros creados en la categoría `Importación bancaria > Por clasificar`.

La previsualización no modifica los movimientos. Una importación confirmada no puede procesarse por segunda vez.

## 9. Captura de comprobantes

La captura acepta una imagen. Si la IA está disponible, intenta sugerir tipo, monto, concepto y fecha. Si no está disponible o no puede leer la imagen, el formulario sigue funcionando manualmente.

Siempre debes revisar:

- tipo de movimiento;
- concepto, monto y fecha;
- categoría y etiquetas;
- cuenta y método de pago;
- si es crédito, acreedor existente, fecha de pago y cuotas.

En esta vista solo se muestran acreedores activos ya registrados. Si necesitas uno nuevo, créalo previamente desde el flujo normal de gasto a crédito. La confirmación conserva el comprobante asociado al movimiento. Cancelar el borrador no crea ningún movimiento.

## 10. Actividad y reportes

**Actividad** reúne ingresos, gastos, transferencias y pagos de deuda. Puede filtrarse por texto, tipo y rango de fechas.

**Análisis** permite seleccionar semana, mes, semestre, año, todo el historial o un rango personalizado. También distingue:

- **Caja:** refleja entradas y salidas efectivas, incluidos pagos de deuda.
- **Consumo:** ayuda a analizar cuándo se originó el gasto, especialmente en compras a crédito.

Los reportes disponibles son:

- CSV financiero para tratamiento tabular;
- PDF con resumen, comparación del periodo anterior, cuentas, presupuestos, composición del gasto, deudas, próximos compromisos y proyección registrada.

Las proyecciones usan saldos, recurrentes y cuotas guardadas; no predicen operaciones ocasionales.

## 11. Asistente financiero

El acceso depende de que un administrador active la IA y autorice tu usuario. El asistente puede:

- consultar movimientos, cuentas, presupuestos, deudas, cuotas, transferencias y recurrentes;
- distinguir fechas de generación de deuda y fechas de pago;
- analizar hábitos y tendencias;
- simular escenarios;
- trabajar con objetivos financieros;
- preparar ingresos o gastos escritos en lenguaje natural.

Para escribir un movimiento, el asistente primero crea un borrador y muestra el resumen. Solo guarda después de una confirmación explícita. Los borradores caducan y solo puede existir uno pendiente por usuario.

Las respuestas y recomendaciones son orientativas. Verifica siempre fechas, importes, categorías y supuestos antes de tomar una decisión financiera.

## 12. Tareas y notificaciones

Las tareas admiten categoría, fecha, horario, prioridad y los estados pendiente, trabajando, finalizada o cancelada.

Las notificaciones incluyen cuotas próximas o vencidas, presupuestos al límite y recomendaciones nuevas. Pueden marcarse individualmente o todas como leídas.

## 13. Perfil, usuarios y roles

Cada persona puede actualizar nombre, correo, teléfono, imagen y su propia contraseña.

- **Usuario normal:** gestiona únicamente sus datos.
- **Staff:** además accede a las pantallas administrativas de usuarios, configuración y consumo de IA.
- **Superusuario:** tiene administración completa de Django y acceso permanente al asistente.

Un miembro staff no puede editar ni restablecer la contraseña de un superusuario si no es también superusuario. Nadie puede desactivar su propio usuario desde la pantalla de administración.

## 14. Instalar como aplicación en Android

1. Publica o abre TaskBudget mediante HTTPS.
2. En Chrome para Android, inicia sesión y abre el menú.
3. Pulsa **Instalar aplicación** o **Agregar a pantalla principal**. También puede aparecer el botón de instalación dentro de TaskBudget.
4. Confirma la instalación.

La PWA se abre en una ventana independiente y conserva la navegación móvil. No es un APK nativo. Sin conexión se muestra una pantalla segura, pero no se pueden consultar ni modificar finanzas hasta recuperar internet.
