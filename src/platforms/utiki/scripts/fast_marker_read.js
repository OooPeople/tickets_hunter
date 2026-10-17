(() => {
            // __ticketsHunterCtbcFastMarkerRead
            try {
                const raw = sessionStorage.getItem(__UTIKI_FAST_MARKER_READ_ARG_0__);
                if (raw === null) return JSON.stringify({
                    available: true, present: false, marker: null,
                    raw: null, page_url: location.href
                });
                try {
                    return JSON.stringify({
                        available: true, present: true, marker: JSON.parse(raw),
                        raw, page_url: location.href
                    });
                } catch (_error) {
                    return JSON.stringify({
                        available: true, present: true, marker: null,
                        raw, page_url: location.href
                    });
                }
            } catch (_error) {
                return JSON.stringify({
                    available: false, present: false, marker: null,
                    page_url: location.href
                });
            }
        })()
