(() => {
    const stateKey = Symbol.for('ticketsHunterUtikiManualSelection');
    const pageUrl = location.href;
    const documentId = String(performance.timeOrigin);
    let state = document.documentElement[stateKey];
    if (!state || state.page_url !== pageUrl || state.document_id !== documentId) {
        state = {page_url: pageUrl, document_id: documentId, revision: 0};
        document.documentElement[stateKey] = state;
        const quantity = target => target instanceof HTMLInputElement &&
            target.matches('#table_tickettype input.numbox[key]');
        const seat = target => target instanceof Element &&
            (target.closest('#TBL td[title]') !== null ||
                target.closest('.ticket button[onclick*="setType"]') !== null);
        document.addEventListener('input', event => {
            if (event.isTrusted && quantity(event.target)) state.revision += 1;
        }, true);
        document.addEventListener('change', event => {
            if (event.isTrusted && quantity(event.target)) state.revision += 1;
        }, true);
        document.addEventListener('click', event => {
            if (event.isTrusted && seat(event.target)) state.revision += 1;
        }, true);
    }
    return JSON.stringify({page_url: pageUrl, document_id: documentId,
        revision: state.revision});
})()
