(() => {
    // __ticketsHunterUtikiCaptchaResetCommit
    const expected = __UTIKI_CAPTCHA_RESET_ARG_0__;
    if (location.href !== expected.pageUrl ||
        String(performance.timeOrigin) !== expected.documentId) return 'stale';
    const visible = element => {
        if (!element || !element.isConnected || element.hidden ||
            element.closest('[hidden], [inert], [aria-hidden="true"]')) {
            return false;
        }
        const style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' &&
            element.getClientRects().length > 0;
    };
    const usable = element => visible(element) && !element.disabled &&
        element.getAttribute('aria-disabled') !== 'true' && !element.readOnly;
    const fields = Array.from(document.querySelectorAll('input#CHK'));
    const images = Array.from(document.querySelectorAll('img#chk_pic'));
    const refreshes = Array.from(document.querySelectorAll('#chang_pic'));
    const dialogs = Array.from(document.querySelectorAll('#dialog-message'));
    if (fields.length !== 1 || !usable(fields[0]) || images.length !== 1 ||
        !visible(images[0]) || !images[0].complete ||
        images[0].naturalWidth <= 0 || images[0].naturalHeight <= 0 ||
        refreshes.length !== 1 ||
        !(refreshes[0] instanceof HTMLButtonElement) ||
        refreshes[0].type !== 'button' || !usable(refreshes[0]) ||
        dialogs.some(visible) ||
        (images[0].currentSrc || images[0].src) !== expected.imageSource) {
        return 'rejected';
    }
    const imageDataUrl = () => {
        try {
            const canvas = document.createElement('canvas');
            canvas.width = images[0].naturalWidth;
            canvas.height = images[0].naturalHeight;
            const context = canvas.getContext('2d');
            if (!context) return '';
            context.drawImage(images[0], 0, 0);
            return canvas.toDataURL('image/png');
        } catch (_) {
            return '';
        }
    };
    if (!expected.imageDataUrl || imageDataUrl() !== expected.imageDataUrl) {
        return 'stale';
    }
    fields[0].value = '';
    fields[0].dispatchEvent(new Event('input', {bubbles: true}));
    fields[0].dispatchEvent(new Event('change', {bubbles: true}));
    if (fields[0].value !== '' ||
        !(refreshes[0] instanceof HTMLButtonElement) ||
        refreshes[0].type !== 'button' || !usable(refreshes[0]) ||
        imageDataUrl() !== expected.imageDataUrl || dialogs.some(visible)) {
        return 'stale';
    }
    refreshes[0].click();
    return 'committed';
})()
