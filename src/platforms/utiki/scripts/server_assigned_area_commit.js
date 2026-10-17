
        (() => {
            // __ticketsHunterUtikiServerAssignedAreaCommit
            const intent = __TICKETS_HUNTER_INTENT_JSON__;
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.getAttribute('aria-hidden') === 'true') {
                    return false;
                }
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.pointerEvents !== 'none' &&
                    element.getClientRects().length > 0;
            };
            const actionable = element => visible(element) &&
                !element.matches(':disabled') &&
                element.getAttribute('aria-disabled') !== 'true';
            const identityValid = () =>
                location.href === intent.pageUrl &&
                String(performance.timeOrigin) === intent.documentId;
            const uniqueVisible = selector => {
                const matches = Array.from(document.querySelectorAll(selector))
                    .filter(visible);
                return matches.length === 1 ? matches[0] : null;
            };
            const controls = () => {
                const root = uniqueVisible('#SeatOptions');
                const server = uniqueVisible('#byServer');
                const manual = uniqueVisible('#byhend');
                const checkboxRoot = uniqueVisible('#ABdiv_ATYPE_CB');
                const checkboxes = checkboxRoot ? Array.from(
                    checkboxRoot.querySelectorAll('#ATYPE_CB')) : [];
                const checkbox = checkboxes.length === 1 ? checkboxes[0] : null;
                if (!root || !server || !manual || !checkboxRoot || !checkbox ||
                    !root.contains(server) || !root.contains(manual) ||
                    !root.contains(checkboxRoot) ||
                    !checkboxRoot.contains(checkbox) ||
                    !visible(root) || !visible(checkboxRoot) ||
                    !actionable(server) || !actionable(manual) ||
                    !checkbox.isConnected || checkbox.disabled ||
                    checkbox.getAttribute('aria-disabled') === 'true' ||
                    checkbox.type !== 'checkbox') {
                    return null;
                }
                return {root, server, manual, checkbox};
            };
            const modeIsUnambiguous = state =>
                state.server.classList.contains('active') !==
                    state.manual.classList.contains('active');
            const normalizeArea = value => (value || '').replace(/\s+/g, '');
            const normalizePrice = value =>
                (value || '').replace(/[\s,$]/g, '');
            const hasTickets = value => {
                const text = (value || '').replace(/\s+/g, '');
                if (!text || text.includes('售完') ||
                    /^(0|0張|無)$/.test(text)) return false;
                if (text === '熱賣中') return true;
                const count = text.match(
                    /^(?:剩餘|尚餘|剩|餘)[:：]?([0-9]+)(?:張)?$/) ||
                    text.match(/^([0-9]+)(?:張)?$/);
                if (!count || count[1].length > 16) return false;
                const available = Number(count[1]);
                return Number.isSafeInteger(available) &&
                    available >= intent.ticketNumber;
            };
            const parseAction = value => {
                const match = (value || '').match(
                    /^\s*(?:javascript:\s*)?(Send|SendA)\(\s*(.*?)\s*\)\s*;?\s*$/);
                if (!match) return null;
                const args = [];
                let remaining = match[2];
                while (remaining) {
                    const argument = remaining.match(
                        /^\s*(?:'([^'\"]*)'|"([^'\"]*)")\s*(?:,\s*|$)/);
                    if (!argument) return null;
                    args.push(argument[1] !== undefined ?
                        argument[1] : argument[2]);
                    remaining = remaining.slice(argument[0].length);
                }
                const expectedLength = match[1] === 'Send' ? 5 : 7;
                if (args.length !== expectedLength) return null;
                return {
                    name: match[1], stableArgs: args.slice(0, -1),
                    status: args.at(-1)
                };
            };
            const parseTitle = value => {
                const match = (value || '').match(
                    /^\s*票區\s*[:：]\s*(.*?)\s+票價\s*[:：]\s*(.*?)\s+尚餘\s*[:：]\s*(.*?)\s*$/);
                return match ? {
                    area: normalizeArea(match[1]),
                    price: normalizePrice(match[2]), status: match[3]
                } : null;
            };
            const targetRow = () => {
                const area = document.querySelector(
                    `#mapdata area#${CSS.escape(intent.areaId)}`);
                if (!area) return null;
                const action = parseAction(area.getAttribute('href'));
                const title = parseTitle(area.getAttribute('title'));
                if (!action || !title || action.name !== intent.actionName ||
                    action.stableArgs.length !== intent.actionArgs.length ||
                    action.stableArgs.some((value, index) =>
                        value !== intent.actionArgs[index]) ||
                    title.area !== normalizeArea(intent.area) ||
                    title.price !== normalizePrice(intent.price) ||
                    !hasTickets(action.status) || !hasTickets(title.status)) {
                    return null;
                }
                const rows = Array.from(document.querySelectorAll(
                    '.seats table.f1 > tbody > tr.saleTr')).filter(row => {
                    if (!(row.getAttribute('rel') || '').split(/\s+/)
                        .includes(intent.areaId) || !actionable(row)) return false;
                    const areaText = row.querySelector(
                        'td[data-title^="票區"]')?.textContent;
                    const priceText = row.querySelector(
                        'td[data-title^="票價"]')?.textContent;
                    const status = row.querySelector(
                        'td[data-title^="空位"]')?.textContent;
                    const rowAction = parseAction(
                        row.getAttribute('onclick'));
                    return normalizeArea(areaText) ===
                        normalizeArea(intent.area) &&
                        normalizePrice(priceText) ===
                        normalizePrice(intent.price) && hasTickets(status) &&
                        rowAction && rowAction.name === intent.actionName &&
                        rowAction.stableArgs.length ===
                            intent.actionArgs.length &&
                        rowAction.stableArgs.every((value, index) =>
                            value === intent.actionArgs[index]) &&
                        hasTickets(rowAction.status);
                });
                return rows.length === 1 ? rows[0] : null;
            };

            const root = document.documentElement;
            const commitMarker = 'data-tickets-hunter-ctbc-area-commit';
            const modePrepareMarker =
                'data-tickets-hunter-utiki-server-mode-prepare';
            const checkboxPrepareMarker =
                'data-tickets-hunter-utiki-server-checkbox-prepare';
            const beginPreparation = markerName => {
                if (root.getAttribute(markerName) === intent.documentId) {
                    return false;
                }
                root.setAttribute(markerName, intent.documentId);
                return true;
            };
            if (!identityValid()) return 'stale';
            if (root.hasAttribute(commitMarker)) {
                return root.getAttribute(commitMarker) ===
                    intent.navigationNonce ? 'committed' : 'stale';
            }
            try {
                if (sessionStorage.getItem(intent.fastMarkerKey) !== null) {
                    return 'stale';
                }
            } catch (_error) {}

            let state = controls();
            if (!state || !modeIsUnambiguous(state)) return 'rejected';
            if (!state.server.classList.contains('active')) {
                if (!beginPreparation(modePrepareMarker)) return 'rejected';
                state.server.click();
                if (!identityValid()) return 'stale';
                state = controls();
            }
            if (!state || !state.server.classList.contains('active') ||
                state.manual.classList.contains('active')) return 'rejected';
            if (state.checkbox.checked) {
                if (!beginPreparation(checkboxPrepareMarker)) return 'rejected';
                state.checkbox.click();
                if (!identityValid()) return 'stale';
                state = controls();
            }
            if (!state || !state.server.classList.contains('active') ||
                state.manual.classList.contains('active') ||
                state.checkbox.checked) return 'rejected';

            const row = targetRow();
            if (!row) return 'rejected';
            root.setAttribute(commitMarker, intent.navigationNonce);
            row.click();
            return 'committed';
        })()
