"use client";

import { ArrowDown, ArrowUp, Download, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { undoComputedColumn, type TableResult } from "@/lib/api";

const PAGE_SIZE = 50;

function toCsv(columns: string[], rows: unknown[][]): string {
  const cell = (value: unknown) => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns, ...rows].map((row) => row.map(cell).join(",")).join("\n");
}

export function DataResultTable({ result }: { result: TableResult }) {
  const [sort, setSort] = useState<{ index: number; desc: boolean } | null>(null);
  const [page, setPage] = useState(0);
  const [undone, setUndone] = useState(false);

  const columns = useMemo(
    () => (undone ? result.columns.filter((c) => c !== result.added_column) : result.columns),
    [result.columns, result.added_column, undone]
  );
  const dropped = undone ? result.columns.indexOf(result.added_column!) : -1;

  const sorted = useMemo(() => {
    if (!sort) return result.rows;
    const factor = sort.desc ? -1 : 1;
    return [...result.rows].sort((a, b) => {
      const x = a[sort.index];
      const y = b[sort.index];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * factor;
      return String(x).localeCompare(String(y)) * factor;
    });
  }, [result.rows, sort]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const visible = sorted.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  function download() {
    const url = URL.createObjectURL(
      new Blob([toCsv(columns, sorted.map((r) => r.filter((_, i) => i !== dropped)))], {
        type: "text/csv",
      })
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${result.table ?? "query-result"}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="my-2 rounded-lg border bg-background">
      <div className="max-h-96 overflow-auto">
        <Table>
          <TableHeader className="sticky top-0 bg-background">
            <TableRow>
              {columns.map((column, index) => {
                const active = sort?.index === index;
                return (
                  <TableHead
                    key={column}
                    onClick={() =>
                      setSort(active && !sort.desc ? { index, desc: true } : { index, desc: false })
                    }
                    className="cursor-pointer whitespace-nowrap select-none"
                    aria-sort={active ? (sort.desc ? "descending" : "ascending") : "none"}
                  >
                    <span className="inline-flex items-center gap-1">
                      {column}
                      {active &&
                        (sort.desc ? (
                          <ArrowDown className="size-3" />
                        ) : (
                          <ArrowUp className="size-3" />
                        ))}
                    </span>
                  </TableHead>
                );
              })}
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {row
                  .filter((_, cellIndex) => cellIndex !== dropped)
                  .map((value, cellIndex) => (
                  <TableCell
                    key={cellIndex}
                    className={
                      typeof value === "number" ? "text-right tabular-nums" : undefined
                    }
                  >
                    {value === null || value === undefined ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      String(value)
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
        <span>
          {result.total_row_count} row{result.total_row_count === 1 ? "" : "s"}
          {result.truncated && " (capped)"}
        </span>
        {pageCount > 1 && (
          <span className="flex items-center gap-1">
            <Button
              size="sm"
              variant="ghost"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              Prev
            </Button>
            {page + 1} / {pageCount}
            <Button
              size="sm"
              variant="ghost"
              disabled={page >= pageCount - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </span>
        )}
        {result.added_column && result.table && !undone && (
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await undoComputedColumn(result.table!, result.added_column!);
              setUndone(true);
            }}
          >
            <Undo2 className="size-3" />
            Undo {result.added_column}
          </Button>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={download}>
          <Download className="size-3" />
          CSV
        </Button>
      </div>

      {result.sql && (
        <details className="border-t px-3 py-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground">SQL</summary>
          <pre className="mt-1 overflow-x-auto font-mono">{result.sql}</pre>
        </details>
      )}
    </div>
  );
}
