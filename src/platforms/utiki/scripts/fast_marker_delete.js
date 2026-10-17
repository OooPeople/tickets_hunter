(() => {
            // __ticketsHunterCtbcFastMarkerDelete
            const expectedNonce = __UTIKI_FAST_MARKER_DELETE_ARG_0__;
            try {
                const raw = sessionStorage.getItem(__UTIKI_FAST_MARKER_DELETE_ARG_1__);
                if (raw === null) return true;
                const marker = JSON.parse(raw);
                if (!marker || marker.nonce !== expectedNonce) return false;
                sessionStorage.removeItem(__UTIKI_FAST_MARKER_DELETE_ARG_2__);
                return true;
            } catch (_error) {
                return false;
            }
        })()
