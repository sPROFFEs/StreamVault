function toggleReset(id) {
    const row = document.getElementById(`reset-row-${id}`);
    const icon = document.getElementById(`icon-key-${id}`); // Note: ID in HTML might need to be added/checked

    if (row.style.display === 'none') {
        row.style.display = 'table-row';
        if (icon) icon.style.color = 'var(--accent-blue)';
    } else {
        row.style.display = 'none';
        if (icon) icon.style.color = '#444';
    }
}
