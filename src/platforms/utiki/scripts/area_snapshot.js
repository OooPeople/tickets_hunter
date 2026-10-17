
        (() => new Promise(resolve => {
            let observer = null;
            let timer = null;
            let poller = null;
            const finish = value => {
                if (observer) observer.disconnect();
                if (timer) clearTimeout(timer);
                if (poller) clearInterval(poller);
                resolve(JSON.stringify(value));
                return true;
            };
            const collect = () => {
                if (!location.pathname.includes('/UTK0204_')) {
                    return finish({ok: false, reason: 'navigation'});
                }
                const rows = Array.from(document.querySelectorAll(
                    '.seats table.f1 > tbody > tr.saleTr'));
                const areas = Array.from(document.querySelectorAll(
                    '#mapdata area[id][href]'));
                const mapReady = typeof mainMap === 'object' && mainMap &&
                    Object.keys(mainMap).length > 0;
                if (!rows.length || !areas.length || !mapReady) return false;
                finish({
                    ok: true,
                    page_url: location.href,
                    document_id: String(performance.timeOrigin),
                    rows: rows.map(row => ({
                        area: (row.querySelector(
                            'td[data-title^="票區"]')?.textContent || '').trim(),
                        price: (row.querySelector(
                            'td[data-title^="票價"]')?.textContent || '').trim(),
                        status: (row.querySelector(
                            'td[data-title^="空位"]')?.textContent || '').trim(),
                        rel: row.getAttribute('rel') || ''
                    })),
                    areas: areas.map(area => ({
                        id: area.getAttribute('id') || '',
                        href: area.getAttribute('href') || '',
                        title: area.getAttribute('title') || ''
                    }))
                });
                return true;
            };
            if (collect()) return;
            observer = new MutationObserver(collect);
            observer.observe(document.documentElement, {
                childList: true, subtree: true
            });
            poller = setInterval(collect, 20);
            timer = setTimeout(
                () => finish({ok: false, reason: 'timeout'}), 2500);
        }))()
