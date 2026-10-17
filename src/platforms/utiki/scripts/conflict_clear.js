
        (() => {
            // __ticketsHunterUtikiConflictClear
            const intent = __TICKETS_HUNTER_CONFLICT_CLEAR_JSON__;
            const normalize = value => String(value || '')
                .replace(/\s+/g, ' ').trim();
            const query = new URLSearchParams(location.search);
            const exactQuery = (name, expected) => {
                const values = query.getAll(name);
                return values.length === 1 && values[0] === expected;
            };
            const routeValid = () => {
                if (intent.pageKind === 'seat') {
                    return intent.reservedRoutes.some(route =>
                        location.origin === route[0] &&
                        location.pathname === route[1]);
                }
                if (intent.pageKind === 'general_admission') {
                    return intent.generalRoutes.some(route =>
                        location.origin === route[0] &&
                        location.pathname === route[1]);
                }
                return intent.pageKind === 'server_assigned_quantity' &&
                    intent.serverPaths.includes(location.pathname) &&
                    exactQuery('ATYPE', 'N');
            };
            const identityValid = () =>
                location.href === intent.pageUrl &&
                String(performance.timeOrigin) === intent.documentId &&
                routeValid() &&
                exactQuery('PERFORMANCE_ID', intent.performanceId) &&
                exactQuery('GROUP_ID', intent.groupId) &&
                exactQuery(
                    'PERFORMANCE_PRICE_AREA_ID', intent.priceAreaId);
            if (!identityValid()) return 'stale';
            const hiddenMatches = (selector, expected, required) => {
                const inputs = Array.from(document.querySelectorAll(selector));
                if (inputs.length > 1 || (required && inputs.length !== 1)) {
                    return false;
                }
                return inputs.length === 0 ||
                    (inputs[0].value || '') === expected;
            };
            if (intent.pageKind === 'seat') {
                const isSelected = seat => {
                    const inline = (seat.getAttribute('style') || '')
                        .toLowerCase();
                    const background = (getComputedStyle(seat)
                        .backgroundImage || '').toLowerCase();
                    return inline.includes('icon_chair_select') ||
                        background.includes('icon_chair_select');
                };
                const inspectSeat = () => {
                    if (!identityValid() || !hiddenMatches(
                        '#PERFORMANCE_ID', intent.performanceId, true) ||
                        !hiddenMatches('#GROUP_ID', intent.groupId, true) ||
                        !hiddenMatches(
                            '#PRICE_AREA_ID', intent.priceAreaId, true)) {
                        return {status: 'stale'};
                    }
                    const products = Array.from(
                        document.querySelectorAll('#PRODUCT_ID'));
                    if (products.length !== 1 ||
                        (products[0].value || '') !== intent.productId) {
                        return {status: 'stale'};
                    }
                    const roots = Array.from(document.querySelectorAll('#TBL'));
                    const ticketRoots = Array.from(
                        document.querySelectorAll('.ticket'));
                    if (roots.length !== 1 || ticketRoots.length !== 1 ||
                        !window.seats || typeof window.seats !== 'object' ||
                        Array.isArray(window.seats)) {
                        return {status: 'rejected'};
                    }
                    const selected = Array.from(
                        roots[0].querySelectorAll('td[title]'))
                        .filter(isSelected);
                    const submitted = Object.values(window.seats).filter(
                        seat => seat && String(seat.A || '') !== '');
                    const counters = Array.from(
                        ticketRoots[0].querySelectorAll('.checkNum'));
                    const countersClear = counters.length > 0 && counters.every(
                        counter => /^\d+$/.test(normalize(counter.textContent)) &&
                            Number(normalize(counter.textContent)) === 0);
                    return {
                        status: 'ok', selected, submitted, countersClear
                    };
                };
                const before = inspectSeat();
                if (before.status !== 'ok') return before.status;
                if (before.selected.some(
                    seat => typeof seat.onclick !== 'function')) {
                    return 'rejected';
                }
                before.selected.forEach(seat => seat.click());
                const after = inspectSeat();
                if (after.status !== 'ok') return 'stale';
                return !after.selected.length && !after.submitted.length &&
                    after.countersClear ?
                    'committed' : 'stale';
            }
            const requiredHidden = intent.pageKind ===
                'server_assigned_quantity';
            const inspectQuantity = () => {
                if (!identityValid() || !hiddenMatches(
                    '#PERFORMANCE_ID', intent.performanceId, requiredHidden) ||
                    !hiddenMatches(
                        '#GROUP_ID', intent.groupId, requiredHidden) ||
                    !hiddenMatches(
                        '#PERFORMANCE_PRICE_AREA_ID', intent.priceAreaId,
                        requiredHidden)) {
                    return {status: 'stale'};
                }
                const products = Array.from(
                    document.querySelectorAll('#PRODUCT_ID'));
                if ((requiredHidden &&
                        (products.length !== 1 ||
                            (products[0].value || '') !== intent.productId)) ||
                    (!requiredHidden && products.length !==
                        (intent.productId ? 1 : 0)) ||
                    (products.length === 1 &&
                        (products[0].value || '') !== intent.productId)) {
                    return {status: 'stale'};
                }
                const roots = Array.from(document.querySelectorAll(
                    '[id="table_tickettype"]'));
                const scoped = Array.from(new Set(roots.flatMap(root =>
                    Array.from(root.querySelectorAll('input.numbox[key]')))));
                const inputs = Array.from(document.querySelectorAll(
                    'input.numbox[key]'));
                const scopedSet = new Set(scoped);
                const keys = inputs.map(
                    input => input.getAttribute('key') || '');
                if (!roots.length || !inputs.length ||
                    inputs.length !== scoped.length ||
                    inputs.some(input => !scopedSet.has(input)) ||
                    keys.some(key => !/^[A-Za-z0-9_-]+$/.test(key)) ||
                    new Set(keys).size !== keys.length) {
                    return {status: 'rejected'};
                }
                return {status: 'ok', inputs};
            };
            const before = inspectQuantity();
            if (before.status !== 'ok') return before.status;
            before.inputs.forEach(input => {
                input.value = '0';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                input.dispatchEvent(new Event('blur', {bubbles: true}));
            });
            const after = inspectQuantity();
            if (after.status !== 'ok') return 'stale';
            return after.inputs.every(input => Number(input.value || 0) === 0) ?
                'committed' : 'stale';
        })()
