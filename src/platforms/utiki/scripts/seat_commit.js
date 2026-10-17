
        (() => {
            // __ticketsHunterUtikiSeatCommit
            const plan = __TICKETS_HUNTER_PLAN_JSON__;
            const markerName = 'data-tickets-hunter-utiki-seat-commit';
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
                if (!element || !element.isConnected ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
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
            const inspect = () => {
                if (location.href !== plan.pageUrl ||
                    String(performance.timeOrigin) !== plan.documentId ||
                    !plan.routes.some(route => location.origin === route[0] &&
                        location.pathname === route[1])) {
                    return {status: 'stale'};
                }
                const query = new URLSearchParams(location.search);
                const exactQuery = (name, expected) => {
                    const values = query.getAll(name);
                    return values.length === 1 && values[0] === expected;
                };
                const exactHidden = (selector, expected) => {
                    const inputs = Array.from(
                        document.querySelectorAll(selector));
                    return inputs.length === 1 &&
                        (inputs[0].value || '') === expected;
                };
                const limitInputs = Array.from(
                    document.querySelectorAll('#QUANTITY_LIMIT'));
                if (!exactQuery('PERFORMANCE_ID', plan.performanceId) ||
                    !exactQuery('GROUP_ID', plan.groupId) ||
                    !exactQuery(
                        'PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) ||
                    !exactHidden('#PERFORMANCE_ID', plan.performanceId) ||
                    !exactHidden('#GROUP_ID', plan.groupId) ||
                    !exactHidden('#PRICE_AREA_ID', plan.priceAreaId) ||
                    !exactHidden('#PRODUCT_ID', plan.productId) ||
                    limitInputs.length !== 1 ||
                    normalize(limitInputs[0].value) !== plan.quantityLimitRaw) {
                    return {status: 'stale'};
                }
                const seatRoots = Array.from(document.querySelectorAll('#TBL'));
                const ticketRoots = Array.from(
                    document.querySelectorAll('.ticket'));
                if (seatRoots.length !== 1 || !visible(seatRoots[0]) ||
                    ticketRoots.length !== 1 || !visible(ticketRoots[0]) ||
                    !window.seats ||
                    typeof window.seats !== 'object' ||
                    Array.isArray(window.seats)) {
                    return {status: 'stale'};
                }
                const buttons = Array.from(ticketRoots[0].querySelectorAll(
                    'button[onclick*="setType"]'));
                const tickets = buttons.map((button, index) => {
                    const matches = Array.from(
                        button.querySelectorAll('.checkNum'));
                    const counter = matches.length === 1 ? matches[0] : null;
                    return {
                        button,
                        fingerprint: ticketFingerprint(button, index, counter),
                        disabled: !!button.disabled || !visible(button) ||
                            button.getAttribute('aria-disabled') === 'true',
                        typeId: counter ? (counter.id || '') : '',
                        typeName: counter ?
                            (counter.getAttribute('val') || '') : '',
                        counterCount: matches.length
                    };
                });
                const seatNodes = Array.from(
                    seatRoots[0].querySelectorAll('td[title]'));
                const seats = seatNodes.map((seat, index) => {
                    const inline = (seat.getAttribute('style') || '')
                        .toLowerCase();
                    const style = getComputedStyle(seat);
                    const background = (style.backgroundImage || '')
                        .toLowerCase();
                    const selected = inline.includes('icon_chair_select') ||
                        background.includes('icon_chair_select');
                    const rect = seat.getBoundingClientRect();
                    return {
                        element: seat,
                        fingerprint: seatFingerprint(seat, index),
                        rowIndex: seat.parentElement ?
                            seat.parentElement.rowIndex : -1,
                        cellIndex: seat.cellIndex,
                        centerX: rect.left + rect.width / 2,
                        centerY: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                        selected,
                        available: !selected && (
                            inline.includes('seat-empty') ||
                            background.includes('seat-empty') ||
                            style.cursor === 'pointer')
                    };
                });
                const counters = Array.from(ticketRoots[0].querySelectorAll(
                    '.checkNum')).map(counter => ({
                    id: counter.id || '', raw: normalize(counter.textContent)
                }));
                const counterIds = counters.map(counter => counter.id);
                const seatKeys = seats.map(seat => key(seat.fingerprint));
                const ticketKeys = tickets.map(ticket => key(ticket.fingerprint));
                if (!tickets.length || counterIds.some(id => !id) ||
                    new Set(counterIds).size !== counterIds.length ||
                    counters.some(counter => !safeCount(counter.raw)) ||
                    new Set(seatKeys).size !== seatKeys.length ||
                    new Set(ticketKeys).size !== ticketKeys.length) {
                    return {status: 'stale'};
                }
                const submitted = Object.values(window.seats)
                    .filter(seat => seat && String(seat.A || '') !== '')
                    .map(seat => ({
                        A: String(seat.A || ''),
                        Y: String(seat.Y || ''),
                        Z: String(seat.Z || '')
                    }));
                return {status: 'ok', tickets, seats, counters, submitted};
            };
            const targetTicket = live => live.tickets.filter(ticket =>
                same(ticket.fingerprint, plan.ticketTypeFingerprint) &&
                ticket.typeId === plan.ticketTypeId &&
                ticket.typeName === plan.ticketTypeName &&
                ticket.counterCount === 1 && !ticket.disabled);
            const targetSeats = (live, fingerprints) => fingerprints.map(
                fingerprint => live.seats.filter(seat =>
                    same(seat.fingerprint, fingerprint)));
            const physicallyAdjacent = (first, second) => {
                const dx = Math.abs(first.centerX - second.centerX);
                const dy = Math.abs(first.centerY - second.centerY);
                const size = Math.max(
                    first.width, first.height, second.width, second.height);
                if ([dx, dy, size].every(Number.isFinite) && size > 0) {
                    const aligned = Math.min(dx, dy) <=
                        Math.max(2, size * 0.35);
                    const close = Math.max(dx, dy) <= size * 1.6;
                    const distinct = Math.max(dx, dy) >= size * 0.35;
                    return aligned && close && distinct;
                }
                const rowDelta = Math.abs(first.rowIndex - second.rowIndex);
                const cellDelta = Math.abs(first.cellIndex - second.cellIndex);
                return (rowDelta === 0 && cellDelta === 1) ||
                    (rowDelta === 1 && cellDelta === 0);
            };
            const targetsAdjacent = live => {
                if (!plan.requirePhysicalAdjacency) return true;
                const matches = targetSeats(
                    live, expectedFingerprints);
                if (matches.some(items => items.length !== 1)) return false;
                const targets = matches.map(items => items[0]);
                return targets.slice(1).every((seat, index) =>
                    physicallyAdjacent(targets[index], seat));
            };
            const countersValid = (live, expected) => {
                const values = live.counters.map(counter => ({
                    id: counter.id, value: Number(counter.raw)
                }));
                const target = values.filter(
                    counter => counter.id === plan.ticketTypeId);
                return target.length === 1 && target[0].value === expected &&
                    values.every(counter => counter.value ===
                        (counter.id === plan.ticketTypeId ? expected : 0));
            };
            const selectedValid = (live, fingerprints) => {
                const selected = live.seats.filter(seat => seat.selected);
                return exactSet(
                    selected.map(seat => seat.fingerprint), fingerprints);
            };
            const payloadValid = (live, expected) =>
                live.submitted.length === expected &&
                live.submitted.every(seat => seat.A === 'S' &&
                    seat.Y === plan.ticketTypeId &&
                    seat.Z === plan.ticketTypeName);
            const selectionValid = (live, fingerprints) =>
                live.status === 'ok' && targetTicket(live).length === 1 &&
                selectedValid(live, fingerprints) && targetsAdjacent(live) &&
                payloadValid(live, fingerprints.length) &&
                countersValid(live, fingerprints.length);
            const cleanValid = live => live.status === 'ok' &&
                !live.seats.some(seat => seat.selected) &&
                live.submitted.length === 0 && countersValid(live, 0);
            const expectedFingerprints = plan.action === 'select' ?
                plan.targetSeatFingerprints : plan.selectedSeatFingerprints;
            const intent = JSON.stringify([
                plan.action === 'clear' ? 'clear' : 'selection',
                plan.ticketTypeFingerprint, plan.ticketTypeId,
                plan.ticketTypeName, plan.ticketNumber, expectedFingerprints,
                plan.requirePhysicalAdjacency
            ]);
            let marker = document.documentElement.getAttribute(markerName);
            let live = inspect();
            if (plan.action === 'ready') {
                if (marker && marker !== intent) return 'stale';
                return selectionValid(live, expectedFingerprints) ?
                    'committed' : 'rejected';
            }
            if (marker) {
                if (plan.action === 'select' && marker === intent &&
                    selectionValid(live, expectedFingerprints)) {
                    return 'committed';
                }
                return 'stale';
            }
            if (live.status !== 'ok') return 'rejected';
            if (plan.action === 'select' && !cleanValid(live)) return 'rejected';
            const planned = targetSeats(live, expectedFingerprints);
            if (planned.some(matches => matches.length !== 1)) return 'rejected';
            if (
                plan.action === 'clear' &&
                plan.requirePhysicalAdjacency && targetsAdjacent(live)
            ) return 'rejected';
            if (plan.action !== 'clear' && !targetsAdjacent(live)) {
                return 'rejected';
            }
            if (plan.action === 'select' && planned.some(
                matches => !matches[0].available || matches[0].selected)) {
                return 'rejected';
            }
            if (plan.action === 'clear' && (
                !selectedValid(live, expectedFingerprints) ||
                planned.some(matches => !matches[0].selected))) {
                return 'rejected';
            }
            document.documentElement.setAttribute(markerName, intent);
            if (document.documentElement.getAttribute(markerName) !== intent) {
                return 'stale';
            }
            if (plan.action === 'select') {
                const tickets = targetTicket(live);
                if (tickets.length !== 1) return 'stale';
                tickets[0].button.click();
            }
            const completed = [];
            for (let index = 0; index < expectedFingerprints.length; index += 1) {
                live = inspect();
                if (live.status !== 'ok' ||
                    document.documentElement.getAttribute(markerName) !== intent ||
                    (plan.action !== 'clear' && !targetsAdjacent(live))) {
                    return 'stale';
                }
                const remaining = expectedFingerprints.slice(index);
                const remainingSeats = targetSeats(live, remaining);
                if (remainingSeats.some(matches => matches.length !== 1)) {
                    return 'stale';
                }
                if (plan.action === 'select') {
                    if (!selectionValid(live, completed) ||
                        remainingSeats.some(matches =>
                            !matches[0].available || matches[0].selected)) {
                        return 'stale';
                    }
                } else {
                    const selected = live.seats.filter(seat => seat.selected);
                    if (!exactSet(
                        selected.map(seat => seat.fingerprint), remaining
                    ) || remainingSeats.some(matches => !matches[0].selected)) {
                        return 'stale';
                    }
                }
                remainingSeats[0][0].element.click();
                completed.push(expectedFingerprints[index]);
            }
            live = inspect();
            if (plan.action === 'select') {
                return selectionValid(live, expectedFingerprints) ?
                    'committed' : 'stale';
            }
            if (!cleanValid(live) ||
                document.documentElement.getAttribute(markerName) !== intent) {
                return 'stale';
            }
            document.documentElement.removeAttribute(markerName);
            return document.documentElement.hasAttribute(markerName) ?
                'stale' : 'committed';
        })()
