window.logoutCurrentSession = async (redirectTo = "/") => {
  const token = localStorage.getItem("token");

  try {
    if (token) {
      const response = await fetch("/api/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        throw new Error(`Logout failed: ${response.status}`);
      }
    }
  } catch (error) {
    console.warn("Could not close server session:", error);
  } finally {
    localStorage.clear();
    window.location.href = redirectTo;
  }
};
