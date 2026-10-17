
        (() => {
            // __ticketsHunterUtikiDateSnapshot
            const roots = Array.from(document.querySelectorAll(
                '#PerformanceListTable'));
            const pageUrl = location.href;
            const documentId = String(performance.timeOrigin);
            if (roots.length !== 1) {
                return JSON.stringify({
                    ok: false, reason: 'scope', page_url: pageUrl,
                    document_id: documentId
                });
            }
            const rows = Array.from(roots[0].querySelectorAll(
                'table > tbody > tr'));
            if (!rows.length) {
                return JSON.stringify({
                    ok: false, reason: 'not-ready', page_url: pageUrl,
                    document_id: documentId
                });
            }
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
            const fingerprint = element => ({
                tag_name: (element.tagName || '').toUpperCase(),
                id: element.getAttribute('id') || '',
                href: element.getAttribute('href') || '',
                resolved_href: element.href || '',
                role: element.getAttribute('role') || '',
                type: element.getAttribute('type') || '',
                target: element.getAttribute('target') || '',
                download: element.getAttribute('download') || '',
                ping: element.getAttribute('ping') || '',
                formaction: element.getAttribute('formaction') || '',
                has_form: !!element.closest('form'),
                onclick: element.getAttribute('onclick') || ''
            });
            return JSON.stringify({
                ok: true,
                page_url: pageUrl,
                document_id: documentId,
                rows: rows.map(row => {
                    const cell = row.lastElementChild;
                    const controls = cell && cell.tagName === 'TD' ?
                        Array.from(cell.querySelectorAll(
                            'button, a[href], [role="button"]')) : [];
                    return {
                        text: (row.innerText || row.textContent || '')
                            .replace(/\s+/g, ' ').trim(),
                        controls: controls.map(control => ({
                            actionable: control.closest('tr') === row &&
                                control.closest('td') === cell &&
                                visible(control) && !control.matches(':disabled') &&
                                control.getAttribute('aria-disabled') !== 'true',
                            ...fingerprint(control)
                        }))
                    };
                })
            });
        })()
