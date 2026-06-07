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
