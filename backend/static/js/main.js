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

// CSRF helper: attach X-CSRFToken header to same-origin fetch POST/PUT/DELETE requests
(() => {
    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    }

    const csrftoken = getCookie('csrftoken');
    if (!csrftoken) return;

    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        try {
            const method = (init && init.method) || 'GET';
            const isSafeMethod = /^(GET|HEAD|OPTIONS|TRACE)$/i.test(method);
            const url = (typeof input === 'string') ? input : input.url;
            const isSameOrigin = url && (url.startsWith(window.location.origin) || url.startsWith('/') );

            if (!isSafeMethod && isSameOrigin) {
                init.headers = init.headers || {};
                if (init.headers instanceof Headers) {
                    init.headers.set('X-CSRFToken', csrftoken);
                } else if (Array.isArray(init.headers)) {
                    init.headers.push(['X-CSRFToken', csrftoken]);
                } else {
                    init.headers['X-CSRFToken'] = init.headers['X-CSRFToken'] || csrftoken;
                }
            }
        } catch (e) {
            // silent fallback
        }
        return originalFetch(input, init);
    };
})();

(function() {
    var toggleButton = document.getElementById('themeToggleButton');
    var root = document.documentElement;

    function setTheme(theme) {
        var isDark = theme === 'dark';
        root.classList.toggle('theme-dark', isDark);
        localStorage.setItem('portalTheme', theme);
        if (toggleButton) {
            toggleButton.innerHTML = isDark
                ? '<i class="fa-solid fa-sun"></i>'
                : '<i class="fa-solid fa-moon"></i>';
            toggleButton.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
        }
    }

    function onToggle() {
        var current = root.classList.contains('theme-dark') ? 'dark' : 'light';
        setTheme(current === 'dark' ? 'light' : 'dark');
    }

    if (toggleButton) {
        var storedTheme = localStorage.getItem('portalTheme');
        var initialTheme = storedTheme || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        setTheme(initialTheme);
        toggleButton.addEventListener('click', onToggle);
    }
})();
