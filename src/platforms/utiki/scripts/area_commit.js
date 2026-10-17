
        (() => {
            // __ticketsHunterCtbcNativeAreaCommit
            const pageUrl = __UTIKI_AREA_COMMIT_ARG_0__;
            const documentId = __UTIKI_AREA_COMMIT_ARG_1__;
            const areaId = __UTIKI_AREA_COMMIT_ARG_2__;
            const expectedArea = __UTIKI_AREA_COMMIT_ARG_3__;
            const expectedPrice = __UTIKI_AREA_COMMIT_ARG_4__;
            const expectedActionName = __UTIKI_AREA_COMMIT_ARG_5__;
            const expectedActionArgs = __UTIKI_AREA_COMMIT_ARG_6__;
            const ticketNumber = __UTIKI_AREA_COMMIT_ARG_7__;
            const navigationNonce = __UTIKI_AREA_COMMIT_ARG_8__;
            const normalizeArea = value => (value || '').replace(/\s+/g, '');
            const normalizePrice = value => (value || '').replace(/[\s,$]/g, '');
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
                    available >= ticketNumber;
            };
            const parseAction = value => {
                const match = (value || '').match(
                    /^\s*javascript:\s*(Send|SendA)\(\s*(.*?)\s*\)\s*;?\s*$/);
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
                if (!match) return null;
                return {
                    area: normalizeArea(match[1]),
                    price: normalizePrice(match[2]), status: match[3]
                };
            };
            const root = document.documentElement;
            const commitMarker = 'data-tickets-hunter-ctbc-area-commit';
            if (location.href !== pageUrl ||
                String(performance.timeOrigin) !== documentId) return 'stale';
            if (root.hasAttribute(commitMarker)) {
                return root.getAttribute(commitMarker) === navigationNonce ?
                    'committed' : 'stale';
            }
            try {
                if (sessionStorage.getItem(__UTIKI_AREA_COMMIT_ARG_9__) !== null) {
                    return 'stale';
                }
            } catch (_error) {}
            const area = document.querySelector(
                `#mapdata area#${CSS.escape(areaId)}`);
            if (!area) return 'rejected';
            const action = parseAction(area.getAttribute('href'));
            const mapTitle = parseTitle(area.getAttribute('title'));
            if (!action || !mapTitle || action.name !== expectedActionName ||
                action.stableArgs.length !== expectedActionArgs.length ||
                action.stableArgs.some((value, index) =>
                    value !== expectedActionArgs[index]) ||
                mapTitle.area !== normalizeArea(expectedArea) ||
                mapTitle.price !== normalizePrice(expectedPrice) ||
                !hasTickets(action.status) ||
                !hasTickets(mapTitle.status)) return 'rejected';
            const rows = Array.from(document.querySelectorAll(
                '.seats table.f1 > tbody > tr.saleTr'));
            const matchingRows = rows.filter(row => {
                if (!(row.getAttribute('rel') || '').split(/\s+/)
                    .includes(areaId)) return false;
                const currentArea = row.querySelector(
                    'td[data-title^="票區"]')?.textContent;
                const currentPrice = row.querySelector(
                    'td[data-title^="票價"]')?.textContent;
                const status = row.querySelector(
                    'td[data-title^="空位"]')?.textContent;
                return normalizeArea(currentArea) ===
                    normalizeArea(expectedArea) &&
                    normalizePrice(currentPrice) ===
                    normalizePrice(expectedPrice) && hasTickets(status);
            });
            if (!matchingRows.length) return 'rejected';
            root.setAttribute(commitMarker, navigationNonce);
            area.click();
            return 'committed';
        })()
