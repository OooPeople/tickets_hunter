(() => {
    const expectedUrl = __UTIKI_MANUAL_ARM_ARG_0__;
    const expectedDocument = __UTIKI_MANUAL_ARM_ARG_1__;
    const consumedPrior = __UTIKI_MANUAL_ARM_ARG_2__;
    const key = 'ticketsHunterUtikiManualSubmit';
    const stateKey = Symbol.for('ticketsHunterUtikiManualArm');
    const pageUrl = location.href;
    const documentId = String(performance.timeOrigin);
    const button = document.querySelectorAll('button#addcart');
    if (pageUrl !== expectedUrl || documentId !== expectedDocument ||
        button.length !== 1 || !button[0].isConnected || button[0].disabled) {
        return JSON.stringify({arm_token: ''});
    }
    let state = document.documentElement[stateKey];
    try {
        const saved = sessionStorage.getItem(key);
        if (saved !== null) {
            let prior = null;
            try { prior = JSON.parse(saved); } catch (_) {}
            if (!consumedPrior || !prior ||
                Object.keys(consumedPrior).sort().join(',') !==
                    'arm_token,click_token,document_id,page_url' ||
                Object.keys(prior).sort().join(',') !==
                    'arm_token,click_token,document_id,page_url' ||
                Object.keys(prior).some(name => prior[name] !== consumedPrior[name])) {
                return JSON.stringify({arm_token: ''});
            }
            sessionStorage.removeItem(key);
        }
    } catch (_) {
        return JSON.stringify({arm_token: ''});
    }
    if (state) {
        state.armed = false;
        if (typeof state.handler === 'function') {
            document.removeEventListener('click', state.handler, true);
        }
        delete document.documentElement[stateKey];
    }
    {
        const armToken = crypto.randomUUID();
        state = {
            page_url: pageUrl, document_id: documentId,
            arm_token: armToken, armed: true
        };
        document.documentElement[stateKey] = state;
        state.handler = event => {
            if (!state.armed) return;
            if (!event.isTrusted || event.target !== button[0] &&
                !(event.target instanceof Node && button[0].contains(event.target))) return;
            if (!button[0].isConnected || button[0].disabled ||
                location.href !== pageUrl || String(performance.timeOrigin) !== documentId) return;
            const record = {
                page_url: pageUrl, document_id: documentId,
                arm_token: armToken, click_token: crypto.randomUUID()
            };
            try {
                if (sessionStorage.getItem(key) === null) {
                    sessionStorage.setItem(key, JSON.stringify(record));
                    state.armed = false;
                }
            } catch (_) {}
        };
        document.addEventListener('click', state.handler, true);
    }
    return JSON.stringify({arm_token: state.arm_token});
})()
