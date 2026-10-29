// Touch dragging for horizontally scrollable boards. Desktop keeps native HTML drag and drop.
window.enableTouchKanban = function ({ board, cardSelector, columnSelector, handleSelector, onDrop }) {
  if (!board || !window.PointerEvent) return;
  let active = null;

  function targetAt(x, y) {
    const column = document.elementFromPoint(x, y)?.closest(columnSelector);
    return column && board.contains(column) ? column : null;
  }

  function updateTarget() {
    if (!active) return;
    const column = targetAt(active.x, active.y);
    if (active.column !== column) {
      active.column?.classList.remove('is-over', 'drag-over');
      active.column = column;
      column?.classList.add(active.overClass);
    }
  }

  function scrollFrame() {
    if (!active) return;
    const rect = board.getBoundingClientRect();
    const edge = 65;
    if (active.y >= rect.top && active.y <= rect.bottom) {
      if (active.x > rect.right - edge) board.scrollLeft += 14;
      if (active.x < rect.left + edge) board.scrollLeft -= 14;
    }
    updateTarget();
    active.frame = requestAnimationFrame(scrollFrame);
  }

  board.querySelectorAll(cardSelector).forEach(card => {
    const handle = handleSelector ? card.querySelector(handleSelector) : card;
    if (!handle) return;
    handle.style.touchAction = 'none';
    handle.addEventListener('pointerdown', event => {
      if (event.pointerType === 'mouse' || active || event.button !== 0) return;
      event.preventDefault();
      const rect = card.getBoundingClientRect();
      const ghost = card.cloneNode(true);
      ghost.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;z-index:10000;pointer-events:none;opacity:.9;box-shadow:0 12px 30px rgba(15,23,42,.25);`;
      document.body.appendChild(ghost);
      card.classList.add('is-dragging');
      active = { card, handle, ghost, id: event.pointerId, x: event.clientX, y: event.clientY,
        offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top,
        column: null, overClass: columnSelector === '.kanban-body' ? 'drag-over' : 'is-over', frame: null };
      handle.setPointerCapture(event.pointerId);
      scrollFrame();
    });
    handle.addEventListener('pointermove', event => {
      if (!active || active.id !== event.pointerId) return;
      event.preventDefault();
      active.x = event.clientX;
      active.y = event.clientY;
      active.ghost.style.left = `${event.clientX - active.offsetX}px`;
      active.ghost.style.top = `${event.clientY - active.offsetY}px`;
      updateTarget();
    });
    const finish = event => {
      if (!active || active.id !== event.pointerId) return;
      const { card, ghost, column, frame } = active;
      cancelAnimationFrame(frame);
      ghost.remove();
      card.classList.remove('is-dragging');
      column?.classList.remove('is-over', 'drag-over');
      active = null;
      if (event.type === 'pointerup' && column) onDrop(card, column);
    };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
  });
};
