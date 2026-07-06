// src/api/agent.ts
import axios from 'axios';

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
export const AUTH_TOKEN_KEY = 'webagent_access_token';
export const AUTH_USER_KEY = 'webagent_user';

axios.interceptors.request.use((config) => {
    const token = localStorage.getItem(AUTH_TOKEN_KEY);
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

axios.interceptors.response.use(
    (response) => response,
    (error) => {
        const status = error?.response?.status;
        const url = String(error?.config?.url ?? '');
        if (status === 401 && !url.includes('/api/auth/')) {
            localStorage.removeItem(AUTH_TOKEN_KEY);
            localStorage.removeItem(AUTH_USER_KEY);
            window.dispatchEvent(new Event('webagent-auth-expired'));
        }
        return Promise.reject(error);
    },
);

export type UserRole = 'admin' | 'user';

export interface AuthUser {
    username: string;
    role: UserRole;
    display_name?: string;
}

export interface LoginResponse {
    access_token: string;
    token_type: 'bearer';
    expires_in: number;
    user: AuthUser;
}

export interface MeResponse {
    user: AuthUser;
}

export interface Citation {
    citation_id?: string;
    title?: string;
    source?: string;
    quote?: string;
    [key: string]: unknown;
}

export interface AgentCase {
    id?: string;
    scenario_id?: string;
    name?: string;
    scenario_name?: string;
    type?: string;
    feature_id?: string;
    documents?: string[];
    citations?: Citation[];
    steps?: string[];
    expectations?: string[];
    [key: string]: unknown;
}

export interface CasesResponse {
    path?: string | null;
    count: number;
    cases: AgentCase[];
}

export interface VerificationSummaryItem {
    scenario_id: string;
    scenario_name: string;
    status: 'passed' | 'failed' | 'ignored';
    passed: boolean;
    reason: string;
}

export interface SummaryResponse {
    test_cases?: {
        count: number;
        path?: string | null;
    };
    verification?: {
        expected_count: number;
        verified_count: number;
        passed_count: number;
        failed_count: number;
        ignored_count: number;
        raw_passed_count?: number;
        raw_failed_count?: number;
        unverified_count?: number;
        pass_rate: number | null;
        items: VerificationSummaryItem[];
    };
    screenshots?: {
        count: number;
    };
    latest_report?: {
        path?: string | null;
        exists?: boolean;
    } | null;
}

export interface ScreenshotItem {
    filename: string;
    path: string;
    url: string;
    scenario_id?: string | null;
    scenario_name?: string;
    status?: string | null;
    name?: string;
    step?: number | null;
    size?: number;
    mtime?: number;
}

export interface ScreenshotsResponse {
    count: number;
    screenshots: string[];
    items: ScreenshotItem[];
}

export interface LogsResponse {
    content: string;
    offset: number;
    exists: boolean;
    path?: string | null;
    size?: number;
}

export interface RunStatusResponse {
    status: string;
    pid?: number | null;
    returncode?: number | null;
    started_at?: number | null;
    log_size?: number;
    log_mtime?: number | null;
    log_idle_seconds?: number | null;
}

export type StartMode = 'resume' | 'full';

export interface StartTestOptions {
    cases?: string[];
    mode?: StartMode;
}

export const agentApi = {
    login: (payload: { username: string; password: string }) => (
        axios.post<LoginResponse>(`${API_BASE}/api/auth/login`, payload)
    ),
    me: () => axios.get<MeResponse>(`${API_BASE}/api/auth/me`),
    // 新增：获取用例列表
    getCases: () => axios.get<CasesResponse | AgentCase[]>(`${API_BASE}/api/cases`),
    // 修改：发送选中的用例 ID 给后端
    startTest: (options: StartTestOptions) => axios.post(`${API_BASE}/api/start-test`, options),
    getRunStatus: () => axios.get<RunStatusResponse>(`${API_BASE}/api/run/status`),
    getLogs: (offset: number) => axios.get<LogsResponse>(`${API_BASE}/api/logs`, { params: { offset } }),
    getScreenshots: (since?: number) => axios.get<ScreenshotsResponse>(`${API_BASE}/api/screenshots`, {
        params: since ? { since } : {},
    }),
    getSummary: () => axios.get<SummaryResponse>(`${API_BASE}/api/summary`),
};

export const getImageUrl = (path: string) => `${API_BASE}${path}`;
