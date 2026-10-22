(() => {
    try {
        const value = JSON.parse(sessionStorage.getItem('ticketsHunterUtikiManualSubmit') || 'null');
        if (!value || Object.keys(value).sort().join(',') !==
            'arm_token,click_token,document_id,page_url' ||
            !Object.values(value).every(item => typeof item === 'string' && item.length > 0)) {
            return 'null';
        }
        return JSON.stringify(value);
    } catch (_) {
        return 'null';
    }
})()
