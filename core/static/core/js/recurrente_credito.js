(() => {
  document.querySelectorAll("form").forEach(form => {
    const tipo = form.querySelector("#id_tipo");
    const metodo = form.querySelector("#id_metodo_pago");
    const acreedor = form.querySelector("#id_acreedor_credito");
    const diaPago = form.querySelector("#id_dia_pago_credito");
    const mesSiguiente = form.querySelector("#id_pago_mes_siguiente");
    const cuenta = form.querySelector("#id_cuenta");
    const descripcionAutomatica = form.querySelector('[data-recurring-mode="automatic"] small');
    if (!tipo || !metodo || !acreedor || !diaPago) return;

    const actualizar = () => {
      const esCredito = tipo.value === "gasto" && metodo.selectedOptions[0]?.dataset.tipo === "credito";
      for (const campo of [acreedor, diaPago, mesSiguiente]) {
        if (!campo) continue;
        campo.closest(".unfold-field").hidden = !esCredito;
        campo.required = esCredito && campo !== mesSiguiente;
      }
      if (cuenta) cuenta.closest(".unfold-field").hidden = esCredito;
      if (descripcionAutomatica) descripcionAutomatica.textContent = esCredito
        ? "Crea una cuota pendiente sin afectar el saldo de la cuenta."
        : "Modifica el saldo al llegar la fecha.";
    };

    tipo.addEventListener("change", actualizar);
    metodo.addEventListener("change", actualizar);
    if (window.jQuery) window.jQuery(metodo).on("change", actualizar);
    actualizar();
  });
})();
