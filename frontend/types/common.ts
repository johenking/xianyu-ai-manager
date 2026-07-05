// API Response Bases
export interface ApiResponse {
  success?: boolean;
  message?: string;
  msg?: string;
  reply?: string;
}

export interface PaginatedResponse<T> {
  success: boolean;
  data: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}
