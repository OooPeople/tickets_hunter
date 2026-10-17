
            (() => {
                const ready = document.readyState === 'interactive' ||
                    document.readyState === 'complete';
                const bodyText = document.body
                    ? (document.body.innerText || document.body.textContent || '')
                        .replace(/\s+/g, ' ').trim().slice(0, 12000)
                    : '';
                const normalRoot = __UTIKI_DOCUMENT_HEALTH_ARG_0__;
                const timeOrigin = Number(performance.timeOrigin ||
                    (performance.timing && performance.timing.navigationStart) || 0);
                const documentToken = Number.isFinite(timeOrigin) && timeOrigin > 0
                    ? String(timeOrigin) : '';
                return JSON.stringify({
                    ready,
                    body_text: bodyText,
                    document_id: documentToken,
                    document_started_at: timeOrigin > 0 ? timeOrigin / 1000 : 0,
                    url: location.href,
                    ready_state: document.readyState,
                    has_normal_root: normalRoot,
                    requires_normal_root: __UTIKI_DOCUMENT_HEALTH_ARG_1__
                });
            })()
