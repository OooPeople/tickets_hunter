
            (() => {
                const image = document.querySelector('img#chk_pic');
                return !!(image && image.src && image.complete && image.naturalWidth > 0);
            })()
