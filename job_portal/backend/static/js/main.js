(() => {
    const roleSelect = document.getElementById("roleSelect");
    const companyField = document.getElementById("companyField");

    const toggleCompanyField = () => {
        if (!roleSelect || !companyField) return;
        if (roleSelect.value === "recruiter") {
            companyField.classList.remove("d-none");
        } else {
            companyField.classList.add("d-none");
        }
    };

    if (roleSelect) {
        toggleCompanyField();
        roleSelect.addEventListener("change", toggleCompanyField);
    }
})();

document.addEventListener("DOMContentLoaded", () => {
    // Helper to get cookies
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // Message Badge Polling
    const unreadBadge = document.getElementById("nav-unread-badge");
    if (unreadBadge) {
        const updateUnreadCount = async () => {
            try {
                const response = await fetch("/messages/api/unread-count/");
                if (response.ok) {
                    const data = await response.json();
                    const count = data.unread_count || 0;
                    if (count > 0) {
                        unreadBadge.textContent = count;
                        unreadBadge.style.display = "inline-block";
                    } else {
                        unreadBadge.style.display = "none";
                    }
                }
            } catch (error) {
                console.error("Error updating unread count:", error);
            }
        };
        updateUnreadCount();
        setInterval(updateUnreadCount, 5000);
    }

    // Notification Badge & Dropdown Polling
    const notifBadge = document.getElementById("nav-notif-badge");
    const notifDropdown = document.getElementById("nav-notif-dropdown-list");
    if (notifBadge && notifDropdown) {
        const updateNotifications = async () => {
            try {
                // Update unread count
                const countRes = await fetch("/notifications/api/unread-count/");
                if (countRes.ok) {
                    const countData = await countRes.json();
                    const count = countData.unread_count || 0;
                    if (count > 0) {
                        notifBadge.textContent = count;
                        notifBadge.style.display = "inline-block";
                    } else {
                        notifBadge.style.display = "none";
                    }
                }

                // Update recent list
                const listRes = await fetch("/notifications/api/recent/");
                if (listRes.ok) {
                    const listData = await listRes.json();
                    const list = listData.notifications || [];

                    // Remove old list elements before the divider
                    const divider = notifDropdown.querySelector(".dropdown-divider");
                    while (notifDropdown.firstChild && notifDropdown.firstChild !== divider) {
                        notifDropdown.removeChild(notifDropdown.firstChild);
                    }

                    if (list.length === 0) {
                        const li = document.createElement("li");
                        li.className = "text-center py-2 text-muted small";
                        li.id = "nav-notif-empty-item";
                        li.textContent = "No new notifications.";
                        notifDropdown.insertBefore(li, divider);
                    } else {
                        list.forEach(n => {
                            const li = document.createElement("li");
                            li.className = "px-3 py-2 border-bottom";
                            li.style.fontSize = "0.85rem";
                            if (!n.is_read) {
                                li.style.backgroundColor = "rgba(34, 197, 94, 0.05)";
                            }
                            li.innerHTML = `
                                <div class="d-flex justify-content-between align-items-start gap-2">
                                    <div>
                                        <div class="fw-bold text-dark text-truncate" style="max-width: 200px;">${n.title}</div>
                                        <div class="text-muted small text-truncate" style="max-width: 200px;">${n.description}</div>
                                        <div class="text-muted mt-1" style="font-size: 0.7rem;"><i class="fa-regular fa-clock me-1"></i>${n.time_ago}</div>
                                    </div>
                                    <a href="/notifications/go/${n.id}/" class="btn btn-xs btn-outline-primary" style="font-size: 0.75rem; padding: 0.1rem 0.4rem;">View</a>
                                </div>
                            `;
                            notifDropdown.insertBefore(li, divider);
                        });
                    }
                }
            } catch (error) {
                console.error("Error updating notifications:", error);
            }
        };
        updateNotifications();
        setInterval(updateNotifications, 5000);
    }
});
