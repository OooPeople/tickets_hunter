(() => {
            // __ticketsHunterCtbcMalformedMarkerDelete
            const expectedRaw = __UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_0__;
            try {
                const current = sessionStorage.getItem(__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_1__);
                if (current !== expectedRaw) return false;
                const confirmed = sessionStorage.getItem(__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_2__);
                if (confirmed !== expectedRaw) return false;
                sessionStorage.removeItem(__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_3__);
                return true;
            } catch (_error) {
                return false;
            }
        })()
