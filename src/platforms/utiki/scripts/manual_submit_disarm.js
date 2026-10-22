(() => {
    const pageUrl = __UTIKI_MANUAL_DISARM_ARG_0__;
    const documentId = __UTIKI_MANUAL_DISARM_ARG_1__;
    const armToken = __UTIKI_MANUAL_DISARM_ARG_2__;
    const key = 'ticketsHunterUtikiManualSubmit';
    const stateKey = Symbol.for('ticketsHunterUtikiManualArm');
    const state = document.documentElement[stateKey];
    if (!state || state.page_url !== pageUrl ||
        state.document_id !== documentId || state.arm_token !== armToken ||
        location.href !== pageUrl || String(performance.timeOrigin) !== documentId) {
        return JSON.stringify({disarmed: false});
    }
    try {
        const marker = JSON.parse(sessionStorage.getItem(key) || 'null');
        if (marker && marker.arm_token === armToken) {
            return JSON.stringify({disarmed: false});
        }
        state.armed = false;
        document.removeEventListener('click', state.handler, true);
        delete document.documentElement[stateKey];
        sessionStorage.removeItem(key);
        return JSON.stringify({disarmed: true});
    } catch (_) {
        return JSON.stringify({disarmed: false});
    }
})()
