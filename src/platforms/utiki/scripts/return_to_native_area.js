(() => {
            // __ticketsHunterCtbcReturn
            const areaUrl = __UTIKI_RETURN_TO_NATIVE_AREA_ARG_0__;
            const sourceUrl = __UTIKI_RETURN_TO_NATIVE_AREA_ARG_1__;
            if (location.href !== sourceUrl) return false;
            location.assign(areaUrl);
            return true;
        })()
