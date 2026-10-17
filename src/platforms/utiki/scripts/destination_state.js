
        (() => new Promise(resolve => {
            // __ticketsHunterCtbcDestinationState
            let observer = null;
            let timer = null;
            let poller = null;
            const finish = value => {
                if (observer) observer.disconnect();
                if (timer) clearTimeout(timer);
                if (poller) clearInterval(poller);
                resolve(JSON.stringify(value));
            };
            const documentToken = () => {
                const value = Number(performance.timeOrigin ||
                    (performance.timing && performance.timing.navigationStart) || 0);
                return Number.isFinite(value) && value > 0 ? String(value) : '';
            };
            const read = () => {
                const markers = __UTIKI_DESTINATION_STATE_ARG_0__;
                const markerReady = markers.every(selector => document.querySelector(selector));
                const exactInput = selector => {
                    const inputs = Array.from(document.querySelectorAll(selector));
                    return inputs.length === 1 ? inputs[0] : null;
                };
                const performanceInput = exactInput('#PERFORMANCE_ID');
                const productInput = exactInput('#PRODUCT_ID');
                const groupInputs = Array.from(
                    document.querySelectorAll('#GROUP_ID'));
                const groupInput = groupInputs[0] || null;
                const priceAreaInput = exactInput(__UTIKI_DESTINATION_STATE_ARG_1__);
                const groupReady = __UTIKI_DESTINATION_STATE_ARG_2__ ?
                    groupInputs.length === 1 : groupInputs.length <= 1;
                const documentId = documentToken();
                if (!documentId || !markerReady || !performanceInput ||
                    !productInput || !groupReady || !priceAreaInput) return false;
                finish({ready: true, page_url: location.href,
                    document_id: documentId,
                    performance_id: performanceInput.value || '',
                    product_id: productInput.value || '',
                    group_id: groupInput ? (groupInput.value || '') : '',
                    price_area_id: priceAreaInput.value || ''});
                return true;
            };
            if (read()) return;
            observer = new MutationObserver(read);
            observer.observe(document.documentElement, {childList: true, subtree: true});
            poller = setInterval(read, 25);
            timer = setTimeout(() => finish({
                ready: false, page_url: location.href,
                document_id: documentToken(), document_ready: document.readyState
            }), 2500);
        }))()
