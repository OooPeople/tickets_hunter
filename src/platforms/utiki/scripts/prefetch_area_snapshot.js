
        (async () => {
            const started = performance.now();
            const areaUrl = __UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_0__;
            const expectedPerformance = __UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_1__;
            const expectedProduct = __UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_2__;
            const mapNamespace = __UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_3__;
            const fail = reason => JSON.stringify({ok: false, reason});
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 1200);
            try {
                const response = await fetch(areaUrl, {
                    credentials: 'include', cache: 'no-store', redirect: 'manual',
                    signal: controller.signal
                });
                if (!response.ok || response.type === 'opaqueredirect' || response.url !== areaUrl) {
                    return fail('area-response');
                }
                const contentType = (response.headers.get('content-type') || '').toLowerCase();
                if (!contentType.startsWith('text/html')) return fail('area-content-type');
                const html = await response.text();
                if (!html || html.length > 250000) return fail('area-size');
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const rows = Array.from(doc.querySelectorAll('.seats table.f1 > tbody > tr.saleTr'));
                if (!rows.length || rows.length > 200 || !doc.querySelector('#mapdata')) return fail('area-markers');
                if (doc.querySelector('#byServer, #ATYPE_CB') || /\bSendA\s*\(/.test(html)) return fail('special-mode');

                const uniqueMatch = regex => {
                    const values = Array.from(html.matchAll(regex), match => match[1]);
                    return values.length === 1 ? values[0] : null;
                };
                const lp = uniqueMatch(/\bvar\s+_lp\s*=\s*['"]([^'"]+)['"]\s*;/g);
                const wt = uniqueMatch(/\bvar\s+_wt\s*=\s*([0-9]+)\s*;/g);
                const season = uniqueMatch(/\bvar\s+seasonTicketId\s*=\s*['"]([^'"]*)['"]\s*;/g);
                const product = uniqueMatch(/\bvar\s+productId\s*=\s*['"]([^'"]+)['"]\s*;/g);
                if (lp !== '0' || wt === null || season !== '' || product !== expectedProduct) return fail('special-flags');

                const mapPattern = /https:\/\/imgs2\.utiki\.com\.tw\/Data\/[A-Za-z0-9_]+\/Images\/Temp\/[A-Za-z0-9_-]+\/1_[A-Za-z0-9_-]+_live\.map\?v=[A-Za-z0-9_-]+/g;
                const mapUrls = Array.from(new Set(html.match(mapPattern) || []));
                if (mapUrls.length !== 1) return fail('map-url-count');
                const mapUrl = new URL(mapUrls[0]);
                const expectedMapPath = `/Data/${mapNamespace}/Images/Temp/${expectedPerformance}/1_${expectedPerformance}_live.map`;
                const mapKeys = Array.from(mapUrl.searchParams.keys());
                const version = mapUrl.searchParams.get('v') || '';
                if (mapUrl.protocol !== 'https:' || mapUrl.hostname !== 'imgs2.utiki.com.tw' ||
                    mapUrl.pathname !== expectedMapPath || mapKeys.length !== 1 || mapKeys[0] !== 'v' ||
                    !/^[A-Za-z0-9_-]{8,128}$/.test(version) || mapUrl.hash) {
                    return fail('map-url');
                }

                const mapResponse = await fetch(mapUrl.href, {
                    credentials: 'omit', cache: 'no-store', redirect: 'error',
                    signal: controller.signal
                });
                if (!mapResponse.ok) return fail('map-response');
                const mapType = (mapResponse.headers.get('content-type') || '').toLowerCase();
                if (!mapType.startsWith('text/plain') && !mapType.startsWith('text/html')) return fail('map-content-type');
                const mapText = await mapResponse.text();
                if (!mapText || mapText.length > 100000) return fail('map-size');
                const mapDoc = new DOMParser().parseFromString(mapText, 'text/html');
                const areas = Array.from(mapDoc.querySelectorAll('area[id][href]')).map(area => ({
                    id: area.getAttribute('id') || '',
                    href: area.getAttribute('href') || '',
                    title: area.getAttribute('title') || ''
                }));
                if (!areas.length || areas.length > 500) return fail('map-markers');
                return JSON.stringify({
                    ok: true,
                    rows: rows.map(row => ({
                        area: (row.querySelector('td[data-title^="票區"]')?.textContent || '').trim(),
                        price: (row.querySelector('td[data-title^="票價"]')?.textContent || '').trim(),
                        status: (row.querySelector('td[data-title^="空位"]')?.textContent || '').trim(),
                        rel: row.getAttribute('rel') || ''
                    })),
                    areas,
                    map_url: mapUrl.href,
                    elapsed_ms: performance.now() - started
                });
            } catch (error) {
                return fail(error?.name === 'AbortError' ? 'timeout' : 'fetch-error');
            } finally {
                clearTimeout(timeout);
            }
        })()
