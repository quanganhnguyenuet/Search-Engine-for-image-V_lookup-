// Shared rules and Vietnamese messages for the auth forms.

/** Keep in sync with "Email OTP length" in the Supabase dashboard. */
export const CODE_LENGTH = 6;
/** Keep in sync with "Minimum password length" in the Supabase dashboard. */
export const MIN_PASSWORD_LENGTH = 8;
/** Supabase refuses to send another code to the same address sooner than this. */
export const RESEND_COOLDOWN_SECONDS = 60;

const MESSAGES: Record<string, string> = {
  invalid_credentials: "Email hoặc mật khẩu không đúng.",
  email_not_confirmed: "Email này chưa được xác nhận.",
  otp_expired: "Mã không đúng hoặc đã hết hạn. Kiểm tra lại hoặc gửi mã mới.",
  weak_password: `Mật khẩu quá yếu. Dùng ít nhất ${MIN_PASSWORD_LENGTH} ký tự, khó đoán hơn.`,
  same_password: "Mật khẩu mới phải khác mật khẩu hiện tại.",
  over_email_send_rate_limit:
    "Chưa có email mới nào được gửi: bạn yêu cầu quá gần lần trước, hoặc hệ thống đã hết lượt gửi. Đợi một lúc rồi thử lại.",
  over_request_rate_limit: "Thử quá nhiều lần. Đợi vài phút rồi thử lại.",
  email_address_invalid: "Địa chỉ email không hợp lệ.",
  validation_failed: "Thông tin nhập chưa hợp lệ.",
  signup_disabled: "Hệ thống đang tạm ngừng nhận đăng ký.",
};

export function authErrorMessage(error: unknown): string {
  const { code, status } = (error ?? {}) as { code?: string; status?: number };
  if (code && Object.hasOwn(MESSAGES, code)) return MESSAGES[code];
  if (status === 429) return MESSAGES.over_request_rate_limit;
  // supabase-js reports network failures with status 0.
  if (status === 0) return "Không kết nối được máy chủ đăng nhập. Kiểm tra mạng rồi thử lại.";
  return "Có lỗi xảy ra. Thử lại sau ít phút.";
}

/** Like authErrorMessage, for requests whose purpose is to send an email. */
export function sendErrorMessage(error: unknown): string {
  const { status } = (error ?? {}) as { status?: number };
  if (status && status >= 500) {
    return "Máy chủ không gửi được email nên chưa có mã mới. Thử lại sau ít phút.";
  }
  return authErrorMessage(error);
}

/** True when Supabase refused to send because of its email limits. */
export function isEmailRateLimit(error: unknown): boolean {
  return (error as { code?: string } | null)?.code === "over_email_send_rate_limit";
}

const PENDING_EMAIL_KEY = "vlookup:pending-email";
const PENDING_ERROR_KEY = "vlookup:pending-send-error";

// The address travels between pages in sessionStorage so that it never appears in a URL.
// Storage can be unavailable; the verify page then asks for the address again.
export function rememberPendingEmail(email: string, sendError = ""): void {
  try {
    window.sessionStorage.setItem(PENDING_EMAIL_KEY, email);
    window.sessionStorage.setItem(PENDING_ERROR_KEY, sendError);
  } catch {}
}

export function readPendingEmail(): { email: string; sendError: string } {
  try {
    return {
      email: window.sessionStorage.getItem(PENDING_EMAIL_KEY) ?? "",
      sendError: window.sessionStorage.getItem(PENDING_ERROR_KEY) ?? "",
    };
  } catch {
    return { email: "", sendError: "" };
  }
}

export function clearPendingEmail(): void {
  try {
    window.sessionStorage.removeItem(PENDING_EMAIL_KEY);
    window.sessionStorage.removeItem(PENDING_ERROR_KEY);
  } catch {}
}

/** Wall-clock time shown next to a send request, to tell the new email from older ones. */
export function clockTime(): string {
  return new Date().toLocaleTimeString("vi-VN", { hour12: false });
}
