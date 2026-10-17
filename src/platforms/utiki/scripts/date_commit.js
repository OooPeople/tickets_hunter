
        (() => {
            // __ticketsHunterUtikiDateCommit
            const intent = __TICKETS_HUNTER_DATE_INTENT_JSON__;
            if (location.href !== intent.pageUrl ||
                String(performance.timeOrigin) !== intent.documentId) {
                return 'stale';
            }
            const root = document.documentElement;
            const commitMarker = 'data-tickets-hunter-utiki-date-commit';
            const priorCommit = root.getAttribute(commitMarker);
            if (priorCommit) {
                return priorCommit === intent.navigationNonce ?
                    'committed' : 'stale';
            }
            const tables = Array.from(document.querySelectorAll(
                '#PerformanceListTable'));
            if (tables.length !== 1) return 'rejected';
            const rows = Array.from(tables[0].querySelectorAll(
                'table > tbody > tr'));
            const row = rows[intent.rowIndex];
            if (!row) return 'rejected';
            const rowText = (row.innerText || row.textContent || '')
                .replace(/\s+/g, ' ').trim();
            if (rowText !== intent.rowText) return 'stale';
            const cell = row.lastElementChild;
            if (!cell || cell.tagName !== 'TD') return 'rejected';
            const controls = Array.from(cell.querySelectorAll(
                'button, a[href], [role="button"]'));
            const control = controls[intent.controlIndex];
            if (!control) return 'rejected';
            const visible = element => {
                if (!element || !element.isConnected ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return element.getClientRects().length > 0 &&
                    style.display !== 'none' && style.visibility !== 'hidden' &&
                    style.pointerEvents !== 'none';
            };
            const actionable = candidate =>
                candidate.closest('tr') === row &&
                candidate.closest('td') === cell && visible(candidate) &&
                !candidate.matches(':disabled') &&
                candidate.getAttribute('aria-disabled') !== 'true';
            const actionableControls = controls.filter(actionable);
            if (actionableControls.length !== 1 ||
                actionableControls[0] !== control) return 'rejected';
            const liveFingerprint = {
                tag_name: (control.tagName || '').toUpperCase(),
                id: control.getAttribute('id') || '',
                href: control.getAttribute('href') || '',
                resolved_href: control.href || '',
                role: control.getAttribute('role') || '',
                type: control.getAttribute('type') || '',
                target: control.getAttribute('target') || '',
                download: control.getAttribute('download') || '',
                ping: control.getAttribute('ping') || '',
                formaction: control.getAttribute('formaction') || '',
                has_form: !!control.closest('form'),
                onclick: control.getAttribute('onclick') || ''
            };
            if (Object.keys(intent.fingerprint).some(key =>
                liveFingerprint[key] !== intent.fingerprint[key])) {
                return 'stale';
            }
            if (intent.fastDestinationUrl) {
                const fastMatches = Array.from(tables[0].querySelectorAll(
                    'button#buy_btn')).filter(candidate =>
                        (candidate.getAttribute('onclick') || '') ===
                            intent.fingerprint.onclick);
                if (fastMatches.length !== 1 || fastMatches[0] !== control) {
                    return 'rejected';
                }
            }
            const cleanupOwnFastMarker = () => {
                try {
                    const raw = sessionStorage.getItem(intent.fastMarkerKey);
                    if (raw === null) return true;
                    const marker = JSON.parse(raw);
                    if (!marker || marker.nonce !== intent.navigationNonce) {
                        return false;
                    }
                    sessionStorage.removeItem(intent.fastMarkerKey);
                    return sessionStorage.getItem(intent.fastMarkerKey) === null;
                } catch (_error) {
                    return false;
                }
            };
            if (intent.fastDestinationUrl) {
                let existingRaw;
                try {
                    existingRaw = sessionStorage.getItem(intent.fastMarkerKey);
                } catch (_error) {
                    return 'rejected';
                }
                if (existingRaw !== null) return 'stale';
                const marker = {
                    version: 1,
                    nonce: intent.navigationNonce,
                    source: intent.pageUrl,
                    destination: intent.fastDestinationUrl,
                    expires_at: Date.now() + intent.fastMarkerTtlMs
                };
                try {
                    sessionStorage.setItem(
                        intent.fastMarkerKey, JSON.stringify(marker));
                    const stored = JSON.parse(
                        sessionStorage.getItem(intent.fastMarkerKey) || 'null');
                    if (!stored || stored.version !== marker.version ||
                        stored.nonce !== marker.nonce ||
                        stored.source !== marker.source ||
                        stored.destination !== marker.destination ||
                        stored.expires_at !== marker.expires_at) {
                        return cleanupOwnFastMarker() ? 'rejected' : 'stale';
                    }
                } catch (_error) {
                    return cleanupOwnFastMarker() ? 'rejected' : 'stale';
                }
            }
            root.setAttribute(commitMarker, intent.navigationNonce);
            if (intent.fastDestinationUrl) {
                location.assign(intent.fastDestinationUrl);
            } else if (intent.actionKind === 'anchor_native') {
                location.assign(intent.targetUrl);
            } else {
                control.click();
            }
            return 'committed';
        })()
