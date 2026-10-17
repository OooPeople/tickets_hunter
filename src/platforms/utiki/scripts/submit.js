
        (() => {
            // __ticketsHunterUtikiSubmit
            const plan = __TICKETS_HUNTER_PLAN_JSON__;
            const normalize = value => String(value || '')
                .replace(/\s+/g, ' ').trim();
            const safeCount = raw => /^[0-9]{1,16}$/.test(raw) &&
                Number.isSafeInteger(Number(raw));
            const same = (first, second) =>
                JSON.stringify(first) === JSON.stringify(second);
            const key = value => JSON.stringify(value);
            const exactSet = (actual, expected) => {
                const left = actual.map(key).sort();
                const right = expected.map(key).sort();
                return same(left, right);
            };
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
            };
            if (location.href !== plan.pageUrl ||
                String(performance.timeOrigin) !== plan.documentId) {
                return 'stale';
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
                const inputs = Array.from(document.querySelectorAll(selector));
                return inputs.length === 1 &&
                    (inputs[0].value || '') === expected;
            };
            const quantityIdentityValid = () => {
                const queryKeys = Array.from(query.keys());
                const routeValid =
                    (plan.pageType === 'general_admission' &&
                        plan.generalRoutes.some(route =>
                            location.origin === route[0] &&
                            location.pathname === route[1])) ||
                    (plan.pageType === 'server_assigned_quantity' &&
                        plan.quantityRoutePaths.includes(location.pathname) &&
                        queryKeys.length === 4 &&
                        new Set(queryKeys).size === 4 &&
                        exactQuery('ATYPE', 'N'));
                const hiddenValid = plan.pageType === 'general_admission' ?
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
                    exactHiddenMatches('#PERFORMANCE_ID', plan.performanceId) &&
                    exactHiddenMatches('#PRODUCT_ID', plan.productId) &&
                    exactHiddenMatches('#GROUP_ID', plan.groupId) &&
                    exactHiddenMatches(
                        '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId);
                return routeValid && hiddenValid &&
                    exactQuery('PERFORMANCE_ID', plan.performanceId) &&
                    exactQuery('GROUP_ID', plan.groupId) &&
                    exactQuery(
                        'PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId);
            };
            const quantityValid = () => {
                if (!quantityIdentityValid()) return false;
                const intent = JSON.stringify([
                    plan.targetKey, plan.ticketNumber]);
                if (document.documentElement.getAttribute(
                    'data-tickets-hunter-utiki-quantity-commit'
                ) !== intent) return false;
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
                    keys.some(item => !/^[A-Za-z0-9_-]+$/.test(item)) ||
                    new Set(keys).size !== keys.length) return false;
                const targets = scoped.filter(input =>
                    input.getAttribute('key') === plan.targetKey);
                const limitInputs = Array.from(
                    document.querySelectorAll('#QUANTITY_LIMIT'));
                if (targets.length !== 1 || limitInputs.length !==
                    (plan.quantityLimitPresent ? 1 : 0)) return false;
                const limitRaw = limitInputs[0] ?
                    normalize(limitInputs[0].value) : '';
                const liveLimit = safeCount(limitRaw) ? Number(limitRaw) : 0;
                if ((plan.quantityLimitPresent && !safeCount(limitRaw)) ||
                    liveLimit !== plan.quantityLimit ||
                    (liveLimit && plan.ticketNumber > liveLimit)) return false;
                const target = targets[0];
                const row = target.closest('tr') ||
                    target.closest('[id="table_tickettype"]');
                const text = normalize(row.innerText || row.textContent);
                const remaining = Array.from(
                    row.querySelectorAll('input[key="LAST_AMOUNT"]'));
                if (normalize(row.querySelector('td.fill')?.textContent) !==
                    plan.targetName || text.includes('售完') ||
                    target.disabled ||
                    target.getAttribute('aria-disabled') === 'true' ||
                    remaining.length !==
                        (plan.targetRemainingPresent ? 1 : 0)) return false;
                if (remaining.length === 1 &&
                    (!safeCount(normalize(remaining[0].value)) ||
                        Number(normalize(remaining[0].value)) <
                            plan.ticketNumber)) return false;
                return inputs.every(input => safeCount(input.value) &&
                    Number(input.value) ===
                    (input === target ? plan.ticketNumber : 0));
            };
            const ticketLabel = button => {
                const clone = button.cloneNode(true);
                clone.querySelectorAll('.checkNum').forEach(node => node.remove());
                return normalize(clone.textContent);
            };
            const ticketFingerprint = (button, index, counter) => [
                index, button.tagName.toLowerCase(), button.id || '',
                button.getAttribute('type') || '',
                button.getAttribute('name') || '',
                button.getAttribute('value') || '',
                button.getAttribute('onclick') || '', ticketLabel(button),
                counter ? (counter.id || '') : '',
                counter ? (counter.getAttribute('val') || '') : ''
            ];
            const seatFingerprint = (seat, index) => [
                index, normalize(seat.getAttribute('title')),
                seat.parentElement ? seat.parentElement.rowIndex : -1,
                seat.cellIndex, seat.id || '',
                seat.getAttribute('onclick') || '',
                seat.getAttribute('data-seat-id') || ''
            ];
            const readSeatState = () => {
                const seatRoots = Array.from(document.querySelectorAll('#TBL'));
                const ticketRoots = Array.from(
                    document.querySelectorAll('.ticket'));
                if (seatRoots.length !== 1 || !visible(seatRoots[0]) ||
                    ticketRoots.length !== 1 || !visible(ticketRoots[0]) ||
                    !window.seats || typeof window.seats !== 'object' ||
                    Array.isArray(window.seats)) return null;
                const tickets = Array.from(ticketRoots[0].querySelectorAll(
                    'button[onclick*="setType"]')).map((button, index) => {
                    const matches = Array.from(
                        button.querySelectorAll('.checkNum'));
                    const counter = matches.length === 1 ? matches[0] : null;
                    return {
                        fingerprint: ticketFingerprint(button, index, counter),
                        disabled: !!button.disabled || !visible(button) ||
                            button.getAttribute('aria-disabled') === 'true',
                        typeId: counter ? (counter.id || '') : '',
                        typeName: counter ?
                            (counter.getAttribute('val') || '') : '',
                        counterCount: matches.length
                    };
                });
                const seats = Array.from(seatRoots[0].querySelectorAll(
                    'td[title]')).map((seat, index) => {
                    const inline = (seat.getAttribute('style') || '')
                        .toLowerCase();
                    const style = getComputedStyle(seat);
                    const background = (style.backgroundImage || '')
                        .toLowerCase();
                    const rect = seat.getBoundingClientRect();
                    return {
                        fingerprint: seatFingerprint(seat, index),
                        rowIndex: seat.parentElement ?
                            seat.parentElement.rowIndex : -1,
                        cellIndex: seat.cellIndex,
                        centerX: rect.left + rect.width / 2,
                        centerY: rect.top + rect.height / 2,
                        width: rect.width, height: rect.height,
                        selected: inline.includes('icon_chair_select') ||
                            background.includes('icon_chair_select')
                    };
                });
                const counters = Array.from(ticketRoots[0].querySelectorAll(
                    '.checkNum')).map(counter => ({
                    id: counter.id || '', raw: normalize(counter.textContent)
                }));
                const submitted = Object.values(window.seats)
                    .filter(seat => seat && String(seat.A || '') !== '')
                    .map(seat => ({
                        A: String(seat.A || ''), Y: String(seat.Y || ''),
                        Z: String(seat.Z || '')
                    }));
                return {tickets, seats, counters, submitted};
            };
            const physicallyAdjacent = (first, second) => {
                const dx = Math.abs(first.centerX - second.centerX);
                const dy = Math.abs(first.centerY - second.centerY);
                const size = Math.max(
                    first.width, first.height, second.width, second.height);
                if ([dx, dy, size].every(Number.isFinite) && size > 0) {
                    return Math.min(dx, dy) <= Math.max(2, size * 0.35) &&
                        Math.max(dx, dy) <= size * 1.6 &&
                        Math.max(dx, dy) >= size * 0.35;
                }
                const rowDelta = Math.abs(first.rowIndex - second.rowIndex);
                const cellDelta = Math.abs(first.cellIndex - second.cellIndex);
                return (rowDelta === 0 && cellDelta === 1) ||
                    (rowDelta === 1 && cellDelta === 0);
            };
            const seatIdentityValid = () => {
                const limitInputs = Array.from(
                    document.querySelectorAll('#QUANTITY_LIMIT'));
                const limitRaw = limitInputs.length === 1 ?
                    normalize(limitInputs[0].value) : '';
                return plan.reservedRoutes.some(route =>
                    location.origin === route[0] &&
                    location.pathname === route[1]) &&
                    exactQuery('PERFORMANCE_ID', plan.performanceId) &&
                    exactQuery('GROUP_ID', plan.groupId) &&
                    exactQuery(
                        'PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) &&
                    exactHiddenMatches('#PERFORMANCE_ID', plan.performanceId) &&
                    exactHiddenMatches('#GROUP_ID', plan.groupId) &&
                    exactHiddenMatches('#PRICE_AREA_ID', plan.priceAreaId) &&
                    exactHiddenMatches('#PRODUCT_ID', plan.productId) &&
                    safeCount(limitRaw) &&
                    (Number(limitRaw) === 0 ||
                        Number(limitRaw) >= plan.ticketNumber);
            };
            const seatValid = () => {
                if (!seatIdentityValid()) return false;
                const live = readSeatState();
                if (!live || !live.tickets.length) return false;
                const ticketKeys = live.tickets.map(item =>
                    key(item.fingerprint));
                const seatKeys = live.seats.map(item => key(item.fingerprint));
                const counterIds = live.counters.map(item => item.id);
                if (new Set(ticketKeys).size !== ticketKeys.length ||
                    new Set(seatKeys).size !== seatKeys.length ||
                    counterIds.some(id => !id) ||
                    new Set(counterIds).size !== counterIds.length ||
                    live.counters.some(item => !safeCount(item.raw))) {
                    return false;
                }
                const tickets = live.tickets.filter(ticket =>
                    same(ticket.fingerprint, plan.ticketTypeFingerprint) &&
                    ticket.typeId === plan.ticketTypeId &&
                    ticket.typeName === plan.ticketTypeName &&
                    ticket.counterCount === 1 && !ticket.disabled);
                const selected = live.seats.filter(seat => seat.selected);
                const matches = plan.expectedSeatFingerprints.map(
                    fingerprint => live.seats.filter(seat =>
                        same(seat.fingerprint, fingerprint)));
                const countersValid = live.counters.every(counter =>
                    Number(counter.raw) ===
                        (counter.id === plan.ticketTypeId ?
                            plan.ticketNumber : 0));
                const payloadValid =
                    live.submitted.length === plan.ticketNumber &&
                    live.submitted.every(seat => seat.A === 'S' &&
                        seat.Y === plan.ticketTypeId &&
                        seat.Z === plan.ticketTypeName);
                const adjacent = !plan.requirePhysicalAdjacency || (
                    matches.every(items => items.length === 1) &&
                    matches.slice(1).every((items, index) =>
                        physicallyAdjacent(matches[index][0], items[0])));
                const intent = JSON.stringify([
                    'selection', plan.ticketTypeFingerprint,
                    plan.ticketTypeId, plan.ticketTypeName,
                    plan.ticketNumber, plan.expectedSeatFingerprints,
                    plan.requirePhysicalAdjacency
                ]);
                const marker = document.documentElement.getAttribute(
                    'data-tickets-hunter-utiki-seat-commit');
                return tickets.length === 1 &&
                    exactSet(selected.map(seat => seat.fingerprint),
                        plan.expectedSeatFingerprints) &&
                    matches.every(items => items.length === 1) &&
                    countersValid && payloadValid && adjacent &&
                    (!marker || marker === intent);
            };
            const commonControls = () => {
                const messages = Array.from(
                    document.querySelectorAll('#dialog-message'))
                    .filter(visible);
                if (messages.length) return null;
                const loginIds = Array.from(
                    document.querySelectorAll('#LOGIN_ID')).filter(visible);
                const loginPasswords = Array.from(
                    document.querySelectorAll('#LOGIN_PWD')).filter(visible);
                if ((loginIds.length || loginPasswords.length) &&
                    (loginIds.length !== 1 || loginPasswords.length !== 1 ||
                        !normalize(loginIds[0].value) ||
                        !(loginPasswords[0].value || ''))) return null;
                const captchas = Array.from(
                    document.querySelectorAll('input#CHK')).filter(visible);
                if (captchas.length !== 1 || captchas[0].disabled ||
                    captchas[0].getAttribute('aria-disabled') === 'true' ||
                    captchas[0].readOnly ||
                    normalize(captchas[0].value).length !== 4) return null;
                const buttons = Array.from(
                    document.querySelectorAll('button#addcart')).filter(visible);
                if (buttons.length !== 1 || buttons[0].disabled ||
                    buttons[0].getAttribute('aria-disabled') === 'true') {
                    return null;
                }
                return buttons[0];
            };
            const stateValid = plan.pageType === 'seat' ?
                seatValid() : quantityValid();
            if (!stateValid) return 'stale';
            const button = commonControls();
            if (!button) return 'rejected';
            if (plan.pageType === 'server_assigned_quantity') {
                const form = button.form;
                if (!form) return 'rejected';
                const method = (
                    button.hasAttribute('formmethod') ?
                        button.formMethod : form.method
                ).toLowerCase();
                if (method !== 'post') return 'rejected';
                const action = new URL(
                    button.hasAttribute('formaction') ?
                        button.formAction : form.action,
                    location.href
                );
                const expectedPath = location.pathname.replace(/\/+$/, '') +
                    '/ADD_SHOPPING_CAR';
                if (action.origin !== location.origin ||
                    action.pathname !== expectedPath ||
                    action.search || action.hash) return 'rejected';
            }
            button.click();
            return 'dispatched';
        })()
