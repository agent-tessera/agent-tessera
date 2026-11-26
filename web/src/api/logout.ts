/** Classic UI logout from the React console. */

/**
 * End the admin session via `POST /logout` and send the browser to login.
 *
 * @param csrf - Token from `GET /v1/me` (cookie sessions).
 */
export async function logout(csrf: string | undefined): Promise<void> {
    const headers = new Headers();
    if (csrf) {
        headers.set("X-CSRF-Token", csrf);
    }
    const body = new FormData();
    if (csrf) {
        body.set("csrf", csrf);
    }
    try {
        await fetch("/logout", {
            method: "POST",
            credentials: "include",
            headers,
            body,
            redirect: "manual",
        });
    } catch {
        // Still clear the client route even if the network call fails.
    }
    window.location.assign("/login");
}
