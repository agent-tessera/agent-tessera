import {
    flexRender,
    getCoreRowModel,
    useReactTable,
    type ColumnDef,
    type OnChangeFn,
    type Row,
    type Table as TanStackTable,
    type VisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

import { EmptyState } from "@/components/EmptyState";
import { cn } from "@/lib/utils";

const ROW_HEIGHT = 36;

export type DataTableProps<TData> = {
    data: TData[];
    // Mixed accessor value types; TanStack's ColumnDef is invariant in TValue.
    columns: ColumnDef<TData, any>[]; // eslint-disable-line @typescript-eslint/no-explicit-any
    /** Fixed viewport height for the scroll body (required for virtualization). */
    height?: number;
    estimateRowHeight?: number;
    loading?: boolean;
    emptyTitle?: string;
    emptyBody?: string;
    emptyAction?: ReactNode;
    className?: string;
    onRowActivate?: (row: TData) => void;
    /** Extra classes per data row (selection highlight, etc.). */
    rowClassName?: (row: TData) => string | undefined;
    /** Extra HTML attributes per data row (`data-*` for tests). */
    getRowAttributes?: (
        row: TData,
    ) => Record<string, string | number | boolean | undefined>;
    /** Stable row id for React keys when data can reorder. */
    getRowId?: (row: TData, index: number) => string;
    columnVisibility?: VisibilityState;
    onColumnVisibilityChange?: OnChangeFn<VisibilityState>;
    /** Fired when the viewport scrolls near the bottom (infinite scroll). */
    onEndReached?: () => void;
    endReachedOffset?: number;
    /** Fired on viewport scroll with the current `scrollTop` (live-tail pause). */
    onViewportScroll?: (scrollTop: number) => void;
    /** When this token changes, scroll the viewport back to the top. */
    scrollResetToken?: number;
    "aria-label"?: string;
};

function LoadingSkeleton({ columns }: { columns: number }) {
    return (
        <div className="space-y-2 p-3" aria-busy="true" aria-label="Loading">
            {Array.from({ length: 6 }, (_, i) => (
                <div
                    key={i}
                    className="bg-muted h-8 animate-pulse rounded-md"
                    style={{ width: `${70 + ((i * 11) % 25)}%` }}
                />
            ))}
            <span className="sr-only">Loading {columns} columns</span>
        </div>
    );
}

function VirtualRows<TData>({
    table,
    rows,
    estimateSize,
    height,
    onRowActivate,
    rowClassName,
    getRowAttributes,
    onEndReached,
    endReachedOffset,
    onViewportScroll,
    scrollResetToken,
}: {
    table: TanStackTable<TData>;
    rows: Row<TData>[];
    estimateSize: number;
    height: number;
    onRowActivate?: (row: TData) => void;
    rowClassName?: (row: TData) => string | undefined;
    getRowAttributes?: (
        row: TData,
    ) => Record<string, string | number | boolean | undefined>;
    onEndReached?: () => void;
    endReachedOffset: number;
    onViewportScroll?: (scrollTop: number) => void;
    scrollResetToken?: number;
}) {
    const parentRef = useRef<HTMLDivElement>(null);
    const virtualizer = useVirtualizer({
        count: rows.length,
        getScrollElement: () => parentRef.current,
        estimateSize: () => estimateSize,
        overscan: 8,
    });

    useEffect(() => {
        if (scrollResetToken === undefined) {
            return;
        }
        const el = parentRef.current;
        if (!el) {
            return;
        }
        el.scrollTop = 0;
    }, [scrollResetToken]);

    useEffect(() => {
        const el = parentRef.current;
        if (!el) {
            return;
        }
        const onScroll = () => {
            onViewportScroll?.(el.scrollTop);
            if (
                onEndReached &&
                el.scrollTop + el.clientHeight >= el.scrollHeight - endReachedOffset
            ) {
                onEndReached();
            }
        };
        el.addEventListener("scroll", onScroll, { passive: true });
        return () => el.removeEventListener("scroll", onScroll);
    }, [onEndReached, endReachedOffset, onViewportScroll, rows.length]);

    const onKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, row: Row<TData>) => {
        if (!onRowActivate) {
            return;
        }
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onRowActivate(row.original);
        }
    };

    return (
        <div
            ref={parentRef}
            className="relative overflow-auto"
            style={{ height }}
            tabIndex={0}
            data-testid="data-table-viewport"
        >
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                <table
                    className="w-full border-collapse text-[13px]"
                    aria-rowcount={rows.length}
                >
                    <tbody>
                        {virtualizer.getVirtualItems().map((virtualRow) => {
                            const row = rows[virtualRow.index];
                            if (!row) {
                                return null;
                            }
                            return (
                                <tr
                                    key={row.id}
                                    data-index={virtualRow.index}
                                    tabIndex={0}
                                    className={cn(
                                        "hover:bg-muted/60 border-border focus-visible:ring-ring absolute left-0 flex w-full border-b outline-none focus-visible:ring-2",
                                        onRowActivate ? "cursor-pointer" : undefined,
                                        rowClassName?.(row.original),
                                    )}
                                    style={{
                                        height: virtualRow.size,
                                        transform: `translateY(${virtualRow.start}px)`,
                                    }}
                                    onClick={
                                        onRowActivate
                                            ? () => onRowActivate(row.original)
                                            : undefined
                                    }
                                    onKeyDown={(event) => onKeyDown(event, row)}
                                    {...getRowAttributes?.(row.original)}
                                >
                                    {row.getVisibleCells().map((cell) => (
                                        <td
                                            key={cell.id}
                                            className="flex items-center px-3 py-1.5"
                                            style={{
                                                width: cell.column.getSize(),
                                                flex: `${cell.column.getSize()} 0 auto`,
                                            }}
                                        >
                                            {flexRender(
                                                cell.column.columnDef.cell,
                                                cell.getContext(),
                                            )}
                                        </td>
                                    ))}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            {/* Keep column metadata reachable for a11y tooling */}
            <div className="sr-only" aria-hidden={false}>
                {table.getRowModel().rows.length} rows
            </div>
        </div>
    );
}

/** Dense virtualized table with a sticky header for large result sets. */
export function DataTable<TData>({
    data,
    columns,
    height = 420,
    estimateRowHeight = ROW_HEIGHT,
    loading = false,
    emptyTitle = "No results",
    emptyBody = "Nothing matches the current filters.",
    emptyAction,
    className,
    onRowActivate,
    rowClassName,
    getRowAttributes,
    getRowId,
    columnVisibility,
    onColumnVisibilityChange,
    onEndReached,
    endReachedOffset = 240,
    onViewportScroll,
    scrollResetToken,
    "aria-label": ariaLabel = "Data table",
}: DataTableProps<TData>) {
    const table = useReactTable({
        data,
        columns,
        getCoreRowModel: getCoreRowModel(),
        getRowId,
        columnResizeMode: "onChange",
        state: {
            columnVisibility,
        },
        onColumnVisibilityChange,
        defaultColumn: {
            minSize: 80,
            size: 140,
        },
    });

    const rows = table.getRowModel().rows;

    return (
        <div
            className={cn(
                "border-border bg-panel overflow-hidden rounded-md border",
                className,
            )}
            aria-label={ariaLabel}
        >
            <div className="overflow-x-auto" tabIndex={0}>
                <table className="w-full border-collapse text-[13px]">
                    <thead className="bg-muted/80 sticky top-0 z-10">
                        {table.getHeaderGroups().map((headerGroup) => (
                            <tr
                                key={headerGroup.id}
                                className="border-border flex w-full border-b"
                            >
                                {headerGroup.headers.map((header) => (
                                    <th
                                        key={header.id}
                                        className="text-muted-foreground relative flex items-center px-3 py-2 text-left text-xs font-semibold tracking-wide uppercase"
                                        style={{
                                            width: header.getSize(),
                                            flex: `${header.getSize()} 0 auto`,
                                        }}
                                    >
                                        {header.isPlaceholder
                                            ? null
                                            : flexRender(
                                                  header.column.columnDef.header,
                                                  header.getContext(),
                                              )}
                                        {header.column.getCanResize() ? (
                                            <div
                                                onMouseDown={header.getResizeHandler()}
                                                onTouchStart={header.getResizeHandler()}
                                                className={cn(
                                                    "absolute top-0 right-0 h-full w-1 cursor-col-resize touch-none select-none",
                                                    header.column.getIsResizing()
                                                        ? "bg-primary"
                                                        : "hover:bg-border-strong bg-transparent",
                                                )}
                                                aria-hidden
                                            />
                                        ) : null}
                                    </th>
                                ))}
                            </tr>
                        ))}
                    </thead>
                </table>
            </div>
            {loading ? (
                <LoadingSkeleton columns={columns.length} />
            ) : rows.length === 0 ? (
                <EmptyState title={emptyTitle} body={emptyBody} action={emptyAction} />
            ) : (
                <VirtualRows
                    table={table}
                    rows={rows}
                    estimateSize={estimateRowHeight}
                    height={height}
                    onRowActivate={onRowActivate}
                    rowClassName={rowClassName}
                    getRowAttributes={getRowAttributes}
                    onEndReached={onEndReached}
                    endReachedOffset={endReachedOffset}
                    onViewportScroll={onViewportScroll}
                    scrollResetToken={scrollResetToken}
                />
            )}
        </div>
    );
}
