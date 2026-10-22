(() => {
    // __ticketsHunterUtikiCaptchaInputGuard
    const expected = __UTIKI_CAPTCHA_INPUT_ARG_0__;
    __UTIKI_NATIVE_INPUT_SETTER_FRAGMENT__
    if (location.href !== expected.pageUrl ||
        String(performance.timeOrigin) !== expected.documentId) return 'stale';
    const visible = element => {
        if (!element || !element.isConnected || element.hidden ||
            element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
        const style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' &&
            element.getClientRects().length > 0;
    };
    const fields = Array.from(document.querySelectorAll('input#CHK'));
    const images = Array.from(document.querySelectorAll('img#chk_pic'));
    const dialogs = Array.from(document.querySelectorAll('#dialog-message'));
    if (fields.length !== 1 || images.length !== 1 ||
        !visible(fields[0]) || fields[0].disabled || fields[0].readOnly ||
        !visible(images[0]) || !images[0].complete ||
        images[0].naturalWidth <= 0 || images[0].naturalHeight <= 0 ||
        dialogs.some(visible) ||
        (images[0].currentSrc || images[0].src) !== expected.imageSource) {
        return 'rejected';
    }
    const revision = document.documentElement[
        Symbol.for('ticketsHunterUtikiCaptchaManualRevision')];
    if (!revision || revision.field !== fields[0] ||
        revision.revision !== expected.manualRevision) return 'stale';
    let dataUrl = '';
    try {
        const canvas = document.createElement('canvas');
        canvas.width = images[0].naturalWidth;
        canvas.height = images[0].naturalHeight;
        const context = canvas.getContext('2d');
        if (!context) return 'rejected';
        context.drawImage(images[0], 0, 0);
        dataUrl = canvas.toDataURL('image/png');
    } catch (_) { return 'rejected'; }
    if (dataUrl !== expected.imageDataUrl) return 'stale';
    if (expected.mode === 'audit') {
        return fields[0].value === expected.answer ? 'audited' : 'stale';
    }
    if (expected.mode !== 'write' || fields[0].value !== '') return 'stale';
    const applied = setNativeInputValue(fields[0], expected.answer);
    if (!applied || revision.revision !== expected.manualRevision ||
        fields[0].value !== expected.answer) return 'partial';
    return 'committed';
})()
