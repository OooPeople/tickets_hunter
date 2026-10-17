
        (() => {
            // __ticketsHunterUtikiQuantityCommit
            const plan = __TICKETS_HUNTER_PLAN_JSON__;
            const safeCount = raw => /^[0-9]{1,16}$/.test(raw) &&
                Number.isSafeInteger(Number(raw));
            const identityValid = () => {
                if (location.href !== plan.pageUrl ||
                    String(performance.timeOrigin) !== plan.documentId) {
                    return false;
                }
                const query = new URLSearchParams(location.search);
                const exactQuery = (key, expected) => {
                    const values = query.getAll(key);
                    return values.length === 1 && values[0] === expected;
                };
                const optionalHiddenMatches = (
                    selector, expected, expectedPresent
                ) => {
                    const inputs = Array.from(
                        document.querySelectorAll(selector));
                    return inputs.length === (expectedPresent ? 1 : 0) &&
                        (!expectedPresent ||
                            (inputs[0].value || '') === expected);
                };
                const exactHiddenMatches = (selector, expected) => {
                    const inputs = Array.from(
                        document.querySelectorAll(selector));
                    return inputs.length === 1 &&
                        (inputs[0].value || '') === expected;
                };
                const routeValid = () => {
                    if (plan.pageType === 'general_admission') {
                        return plan.generalRoutes.some(route =>
                            location.origin === route[0] &&
                            location.pathname === route[1]);
                    }
                    if (plan.pageType !== 'server_assigned_quantity') {
                        return false;
                    }
                    const keys = Array.from(query.keys());
                    return plan.routePaths.includes(location.pathname) &&
                        keys.length === 4 &&
                        new Set(keys).size === 4 &&
                        exactQuery('ATYPE', 'N');
                };
                const hiddenIdentityValid =
                    plan.pageType === 'general_admission' ?
                        optionalHiddenMatches(
                            '#PERFORMANCE_ID', plan.performanceId,
                            plan.performanceHiddenPresent) &&
                        optionalHiddenMatches(
                            '#PRODUCT_ID', plan.productId,
                            plan.productHiddenPresent) &&
                        optionalHiddenMatches(
                            '#GROUP_ID', plan.groupId,
                            plan.groupHiddenPresent) &&
                        optionalHiddenMatches(
                            '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId,
                            plan.priceAreaHiddenPresent) :
                        exactHiddenMatches(
                            '#PERFORMANCE_ID', plan.performanceId) &&
                        exactHiddenMatches('#PRODUCT_ID', plan.productId) &&
                        exactHiddenMatches('#GROUP_ID', plan.groupId) &&
                        exactHiddenMatches(
                            '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId);
                return exactQuery('PERFORMANCE_ID', plan.performanceId) &&
                    exactQuery('GROUP_ID', plan.groupId) &&
                    exactQuery('PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) &&
                    hiddenIdentityValid && routeValid();
            };
            const inspectLive = () => {
                if (!identityValid()) return {status: 'stale'};
                const roots = Array.from(document.querySelectorAll(
                    '[id="table_tickettype"]'));
                const scoped = Array.from(new Set(roots.flatMap(root =>
                    Array.from(root.querySelectorAll('input.numbox[key]')))));
                const inputs = Array.from(document.querySelectorAll(
                    'input.numbox[key]'));
                const scopedSet = new Set(scoped);
                const keys = inputs.map(input =>
                    input.getAttribute('key') || '');
                if (!roots.length || !inputs.length ||
                    inputs.length !== scoped.length ||
                    inputs.some(input => !scopedSet.has(input)) ||
                    keys.some(key => !/^[A-Za-z0-9_-]+$/.test(key)) ||
                    new Set(keys).size !== keys.length) {
                    return {status: 'rejected'};
                }
                const targets = scoped.filter(input =>
                    input.getAttribute('key') === plan.targetKey);
                if (targets.length !== 1) return {status: 'rejected'};
                const limitInputs = Array.from(
                    document.querySelectorAll('#QUANTITY_LIMIT'));
                if (limitInputs.length !==
                    (plan.quantityLimitPresent ? 1 : 0)) {
                    return {status: 'rejected'};
                }
                const limitInput = limitInputs[0] || null;
                const limitRaw = limitInput ?
                    (limitInput.value || '').trim() : '';
                if (plan.quantityLimitPresent && !safeCount(limitRaw)) {
                    return {status: 'rejected'};
                }
                const liveLimit = limitRaw ? Number(limitRaw) : 0;
                if (liveLimit !== plan.quantityLimit ||
                    (liveLimit && plan.ticketNumber > liveLimit)) {
                    return {status: 'rejected'};
                }
                const target = targets[0];
                const row = target.closest('tr') ||
                    target.closest('[id="table_tickettype"]');
                const name = (row.querySelector('td.fill')?.textContent || '')
                    .trim();
                const text = (row.innerText || row.textContent || '')
                    .replace(/\s+/g, ' ').trim();
                const remaining = Array.from(
                    row.querySelectorAll('input[key="LAST_AMOUNT"]'));
                if (name !== plan.targetName || text.includes('售完') ||
                    target.disabled ||
                    target.getAttribute('aria-disabled') === 'true') {
                    return {status: 'rejected'};
                }
                if (remaining.length !==
                    (plan.targetRemainingPresent ? 1 : 0)) {
                    return {status: 'rejected'};
                }
                if (remaining.length === 1) {
                    const raw = (remaining[0].value || '').trim();
                    if (!safeCount(raw) ||
                        Number(raw) < plan.ticketNumber) {
                        return {status: 'rejected'};
                    }
                }
                return {status: 'ok', inputs, target};
            };
            const markerName = 'data-tickets-hunter-utiki-quantity-commit';
            const intent = JSON.stringify([
                plan.targetKey, plan.ticketNumber]);
            const marker = document.documentElement.getAttribute(markerName);
            let live = inspectLive();
            if (live.status !== 'ok') return marker ? 'stale' : live.status;
            const auditValid = state => state.inputs.every(input =>
                safeCount(input.value) && Number(input.value) ===
                    (input === state.target ? plan.ticketNumber : 0));
            if (marker) {
                return marker === intent && auditValid(live) ?
                    'committed' : 'stale';
            }
            document.documentElement.setAttribute(markerName, intent);
            for (const input of live.inputs) {
                input.value = '0';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
            }
            live.target.value = String(plan.ticketNumber);
            live.target.dispatchEvent(new Event('input', {bubbles: true}));
            live.target.dispatchEvent(new Event('change', {bubbles: true}));
            live.target.dispatchEvent(new Event('blur', {bubbles: true}));
            live = inspectLive();
            return live.status === 'ok' && auditValid(live) ?
                'committed' : 'stale';
        })()
