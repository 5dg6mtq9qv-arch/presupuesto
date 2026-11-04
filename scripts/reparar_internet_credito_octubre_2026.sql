-- Ejecutar despues de aplicar las migraciones core.0052 y core.0053.
-- Corrige la compra confirmada de Internet del 04/10/2026 y crea UNA cuota
-- pendiente para Visa Pacifico el 18/11/2026. Activa la generacion
-- automatica mensual: compra el dia 4, cuota el 18 del mes siguiente.
-- El bloque es atomico: ante cualquier discrepancia no se guarda nada.
DO $reparacion$
DECLARE
    v_usuario_id integer;
    v_recurrente_id bigint;
    v_metodo_id bigint;
    v_acreedor_id bigint;
    v_movimiento record;
    v_deuda_id bigint;
BEGIN
    SELECT id INTO STRICT v_usuario_id
    FROM auth_user WHERE username = 'cristian_perugachi';

    SELECT r.id, r.metodo_pago_id INTO STRICT v_recurrente_id, v_metodo_id
    FROM finanzas.movimiento_recurrente r
    JOIN finanzas.metodo_pago m ON m.id = r.metodo_pago_id
    WHERE r.usuario_id = v_usuario_id
      AND r.concepto = 'Pago mensual internet'
      AND r.tipo = 'gasto' AND r.activo = TRUE AND m.tipo = 'credito'
    FOR UPDATE OF r;

    SELECT id INTO STRICT v_acreedor_id
    FROM deudas.acreedor
    WHERE usuario_id = v_usuario_id AND nombre = 'TARJETA VISA PACIFICO';

    SELECT id, concepto, monto, fecha, categoria_id INTO STRICT v_movimiento
    FROM movimientos_financieros.movimiento_financiero
    WHERE usuario_id = v_usuario_id AND recurrente_id = v_recurrente_id
      AND fecha = DATE '2026-10-04' AND tipo = 'gasto'
      AND estado = 'confirmado' AND monto = 16.39
    FOR UPDATE;

    IF EXISTS (
        SELECT 1 FROM deudas.deuda WHERE movimiento_origen_id = v_movimiento.id
    ) THEN
        RAISE EXCEPTION 'La compra ya tiene una deuda; no se duplicara la cuota';
    END IF;

    UPDATE finanzas.movimiento_recurrente
    SET acreedor_credito_id = v_acreedor_id, dia_pago_credito = 18,
        pago_mes_siguiente = TRUE, aplicar_automaticamente = TRUE,
        cuenta_id = NULL
    WHERE id = v_recurrente_id;

    UPDATE movimientos_financieros.movimiento_financiero
    SET metodo_pago_id = v_metodo_id, acreedor_credito_id = v_acreedor_id,
        fecha_pago = DATE '2026-11-18', numero_cuotas_credito = 1,
        cuenta_id = NULL
    WHERE id = v_movimiento.id;

    INSERT INTO deudas.deuda (
        acreedor, concepto, monto_inicial, saldo_actual, fecha_inicio,
        fecha_primera_cuota, fecha_vencimiento, estado, nota, creado,
        usuario_id, categoria_id, numero_cuotas, cuotas_pagadas_previas,
        acreedor_entidad_id, movimiento_origen_id
    ) VALUES (
        'TARJETA VISA PACIFICO', v_movimiento.concepto,
        v_movimiento.monto, v_movimiento.monto, v_movimiento.fecha,
        DATE '2026-11-18', DATE '2026-11-18', 'activa',
        'Generada al corregir la compra recurrente de Internet a credito.', NOW(),
        v_usuario_id, v_movimiento.categoria_id, 1, 0,
        v_acreedor_id, v_movimiento.id
    ) RETURNING id INTO v_deuda_id;

    INSERT INTO pagos_deudas.pago_deuda (
        monto, fecha, nota, creado, deuda_id, cuota_numero, estado
    ) VALUES (
        v_movimiento.monto, DATE '2026-11-18',
        'Cuota pendiente de la compra de Internet del 04/10/2026.', NOW(),
        v_deuda_id, 1, 'pendiente'
    );

    RAISE NOTICE 'Compra % corregida; deuda % y cuota del 18/11/2026 creadas',
        v_movimiento.id, v_deuda_id;
END;
$reparacion$;
