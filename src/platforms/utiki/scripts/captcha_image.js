
            (() => {
                const image = document.querySelector('img#chk_pic');
                if (!image || !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0) {
                    return '';
                }
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                const context = canvas.getContext('2d');
                if (!context) return '';
                context.drawImage(image, 0, 0, canvas.width, canvas.height);
                return canvas.toDataURL('image/png');
            })()
