(() => {
    // __ticketsHunterUtikiCaptchaResetObserve
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
    const revisionKey = Symbol.for('ticketsHunterUtikiCaptchaManualRevision');
    let revisionState = document.documentElement[revisionKey];
    if (!revisionState) {
        revisionState = {field: null, revision: 0};
        document.documentElement[revisionKey] = revisionState;
    }
    if (fields.length === 1 && revisionState.field !== fields[0]) {
        revisionState.field = fields[0];
        revisionState.revision++;
        fields[0].addEventListener('input', event => {
            if (event.isTrusted) revisionState.revision++;
        }, true);
    }
    const images = Array.from(document.querySelectorAll('img#chk_pic'));
    const refreshes = Array.from(document.querySelectorAll('#chang_pic'));
    const dialogs = Array.from(document.querySelectorAll('#dialog-message'));
    let imageDataUrl = '';
    let imageSource = '';
    if (images.length === 1 && visible(images[0]) && images[0].complete &&
        images[0].naturalWidth > 0 && images[0].naturalHeight > 0) {
        try {
            const canvas = document.createElement('canvas');
            canvas.width = images[0].naturalWidth;
            canvas.height = images[0].naturalHeight;
            const context = canvas.getContext('2d');
            if (context) {
                context.drawImage(images[0], 0, 0);
                imageDataUrl = canvas.toDataURL('image/png');
                if (imageDataUrl.length > 2000000) imageDataUrl = '';
                if (imageDataUrl) imageSource = images[0].currentSrc || images[0].src;
            }
        } catch (_) {
            imageDataUrl = '';
        }
    }
    return JSON.stringify({
        page_url: location.href,
        document_id: String(performance.timeOrigin),
        image_source: imageSource,
        image_data_url: imageDataUrl,
        field_usable: fields.length === 1 && usable(fields[0]),
        field_empty: fields.length === 1 && fields[0].value === '',
        manual_value_ready: fields.length === 1 && usable(fields[0]) &&
            /^[A-Za-z0-9]{4}$/.test(fields[0].value),
        manual_revision: revisionState.revision,
        refresh_usable: refreshes.length === 1 &&
            refreshes[0] instanceof HTMLButtonElement &&
            refreshes[0].type === 'button' && usable(refreshes[0]),
        dialog_visible: dialogs.some(visible)
    });
})()
