(() => {
  const editor = document.getElementById('lab-editor');
  const channel = document.getElementById('lab-channel');
  const status = document.getElementById('lab-status');
  const properties = document.getElementById('lab-properties');
  const library = document.getElementById('pc-library');
  const documentControls = document.querySelectorAll('[data-document]');
  const dialogs = document.querySelectorAll('dialog');
  let previousDocument = document.getElementById('lab-document-name').textContent;

  function synchronize() {
    const unavailable = editor.hidden || channel.disabled;
    documentControls.forEach(control => { control.disabled = unavailable; });
    document.getElementById('lab-download').setAttribute('aria-disabled', String(unavailable));
    dialogs.forEach(dialog => {
      let message = dialog.querySelector('.pc-dialog-status');
      if (!message) {
        message = document.createElement('p');
        message.className = 'pc-dialog-status';
        message.setAttribute('aria-live', 'polite');
        dialog.append(message);
      }
      message.textContent = status.textContent;
    });
    const name = document.getElementById('lab-document-name').textContent;
    if (!editor.hidden && library.open && name !== previousDocument) library.close();
    previousDocument = name;
  }

  document.querySelectorAll('[data-dialog]').forEach(button => {
    button.addEventListener('click', () => document.getElementById(button.dataset.dialog).showModal());
  });
  document.querySelectorAll('[data-close]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });
  document.getElementById('lab-download').addEventListener('click', event => {
    if (event.currentTarget.getAttribute('aria-disabled') === 'true') event.preventDefault();
  });
  document.querySelector('[data-fit]').addEventListener('click', () => {
    const zoom = document.getElementById('lab-zoom');
    zoom.value = 'fit';
    zoom.dispatchEvent(new Event('change'));
    document.getElementById('lab-stage').focus();
  });
  const viewport = document.getElementById('lab-viewport');
  const stage = document.getElementById('lab-stage');
  let hand = false, space = false, pan = null;
  let tool = 'move';
  const toolNames = {move:'이동 · V',text:'글자 · T',photo:'사진 교체 · P',transform:'크기·회전 · F',crop:'사진 자르기 · C',shape:'도형 채우기 · U',gradient:'그라데이션 · G',eyedropper:'색상 추출 · I',hand:'화면 이동 · H',zoom:'확대 · 클릭 / 축소 · Alt+클릭'};
  function chooseTool(next) {
    tool = next;
    hand = next === 'hand';
    space = false;
    document.querySelectorAll('[data-tool]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tool === next)));
    document.getElementById('pc-tool-name').textContent = toolNames[next];
    viewport.style.cursor = hand ? 'grab' : next === 'zoom' ? 'zoom-in' : next === 'eyedropper' ? 'crosshair' : '';
    stage.dispatchEvent(new CustomEvent('studio-tool', {detail:next}));
    if (['move','hand','zoom','eyedropper'].includes(next)) stage.focus();
  }
  document.querySelectorAll('[data-tool]').forEach(button => button.addEventListener('click', () => chooseTool(button.dataset.tool)));
  function zoomStep(out=false) {
    const zoom = document.getElementById('lab-zoom');
    const current = stage.getBoundingClientRect().width / Number(stage.style.aspectRatio.split('/')[0].trim());
    const values = [...zoom.options].map(option => Number(option.value)).filter(Number.isFinite).sort((a,b)=>a-b);
    zoom.value = String(out ? [...values].reverse().find(value=>value<current-.001)??values[0] : values.find(value=>value>current+.001)??values.at(-1));
    zoom.dispatchEvent(new Event('change'));
  }
  function stopPan() {
    space = false;
    pan = null;
    viewport.style.cursor = hand ? 'grab' : tool === 'zoom' ? 'zoom-in' : tool === 'eyedropper' ? 'crosshair' : '';
  }
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
    if ([...dialogs].some(dialog => dialog.open)) return;
    const key = event.key.toLowerCase();
    const command = event.ctrlKey || event.metaKey;
    const typing = event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])');
    if (event.altKey || (typing && !(command && key === 's' && !event.shiftKey))) return;
    let action;
    if (command && !event.shiftKey && key === 'o') action = () => library.showModal();
    if (!editor.hidden) {
      if (command && key === 'z') action = () => document.getElementById(event.shiftKey ? 'lab-redo' : 'lab-undo').click();
      if (command && !event.shiftKey && key === 's') action = () => document.getElementById('lab-render').click();
      if (command && (!event.shiftKey || key === '+') && ['0', '1', '=', '+', '-'].includes(key)) action = () => {
        const zoom = document.getElementById('lab-zoom');
        const current = stage.getBoundingClientRect().width / Number(stage.style.aspectRatio.split('/')[0].trim());
        const values = [...zoom.options].map(option => Number(option.value)).filter(Number.isFinite).sort((left, right) => left - right);
        zoom.value = key === '0' ? 'fit' : key === '1' ? '1' : String(key === '-' ? [...values].reverse().find(value => value < current - .001) ?? values[0] : values.find(value => value > current + .001) ?? values.at(-1));
        zoom.dispatchEvent(new Event('change'));
      };
      if (command && !event.shiftKey && ['[', ']'].includes(key)) action = () => properties.querySelector(`[data-order="${key === ']' ? 1 : -1}"]`)?.click();
      const keys = {v:'move',t:'text',p:'photo',f:'transform',c:'crop',u:'shape',g:'gradient',i:'eyedropper',h:'hand',z:'zoom'};
      if (!command && !event.shiftKey && (keys[key] || key === ' ')) action = () => {
        if (key === ' ') {space = true; viewport.style.cursor = 'grab';}
        else chooseTool(keys[key]);
      };
    }
    if (action) {
      event.preventDefault();
      event.stopPropagation();
      if (!channel.disabled && !event.repeat) action();
    }
  }, true);
  document.addEventListener('keyup', event => { if (event.key === ' ') stopPan(); });
  window.addEventListener('blur', stopPan);
  viewport.addEventListener('pointerdown', event => {
    if (!(hand || space) || editor.hidden || channel.disabled || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    pan = {x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
    viewport.setPointerCapture(event.pointerId);
    viewport.style.cursor = 'grabbing';
  }, true);
  viewport.addEventListener('pointermove', event => {
    if (!pan) return;
    viewport.scrollLeft = pan.left + pan.x - event.clientX;
    viewport.scrollTop = pan.top + pan.y - event.clientY;
  });
  viewport.addEventListener('pointerup', stopPan);
  viewport.addEventListener('pointercancel', stopPan);
  viewport.addEventListener('lostpointercapture', stopPan);
  viewport.addEventListener('click', event => {
    if (hand || space || tool === 'zoom') { event.preventDefault(); event.stopPropagation(); if(tool === 'zoom' && !channel.disabled) zoomStep(event.altKey); }
  }, true);
  const shortcuts = document.createElement('p');
  shortcuts.textContent = '단축키: Ctrl/⌘+O 보관함 · Ctrl/⌘+S 초안 저장 · Ctrl/⌘+Z 실행 취소 · Ctrl/⌘+Shift+Z 다시 실행 · Ctrl/⌘+0 화면 맞춤 · Ctrl/⌘+1 100% · Ctrl/⌘ +/- 확대·축소 · Ctrl/⌘+[ / ] 레이어 뒤로·앞으로 · V 이동 · T 글자 속성 · G 선형 그라데이션 속성 · H 또는 Space+드래그 화면 이동. V로 이동 도구 복귀. 입력 중에는 저장 외 단축키를 가로채지 않습니다. C 사진 자르기 · U 도형 채우기 · I 색상 추출 · Z 확대(Alt+클릭 축소) · P 사진 교체 · F 크기·회전. 도구는 기존 레이어를 편집합니다. 새 레이어 생성·브러시·마스크·선택 영역은 미지원입니다. 브라우저가 먼저 처리하는 키는 메뉴를 사용하세요.';
  document.getElementById('pc-help').append(shortcuts);
  const observer = new MutationObserver(synchronize);
  observer.observe(editor, {attributes: true, attributeFilter: ['hidden']});
  observer.observe(channel, {attributes: true, attributeFilter: ['disabled']});
  observer.observe(status, {childList: true, characterData: true, subtree: true});
  observer.observe(document.getElementById('lab-document-name'), {childList: true});
  synchronize();
})();
