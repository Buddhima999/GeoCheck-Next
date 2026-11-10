import { del } from "@vercel/blob";
import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { timingSafeEqual } from "node:crypto";

const MAX_UPLOAD_BYTES = 512 * 1024 * 1024;
const BLOB_PATH = /^geocheck\/[0-9a-f]{32}\.csv$/;

function authorized(value: string | null): boolean {
  const expected = (process.env.GEOCHECK_API_KEY || "").trim();
  if (!expected || !value) return false;
  const supplied = Buffer.from(value);
  const configured = Buffer.from(expected);
  return supplied.length === configured.length && timingSafeEqual(supplied, configured);
}

export default async function handler(request: Request): Promise<Response> {
  if (request.method !== "POST") return Response.json({ error: "Method not allowed." }, { status: 405 });

  try {
    const body = (await request.json()) as HandleUploadBody & {
      action?: string;
      pathname?: string;
    };

    if (body.action === "delete") {
      if (!authorized(request.headers.get("x-api-key"))) {
        return Response.json({ error: "Unauthorized." }, { status: 401 });
      }
      if (!body.pathname || !BLOB_PATH.test(body.pathname)) {
        return Response.json({ error: "Invalid CSV path." }, { status: 400 });
      }
      await del(body.pathname);
      return Response.json({ deleted: true });
    }

    const result = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname, clientPayload) => {
        let clientApiKey = "";
        try {
          const payload = JSON.parse(clientPayload || "{}") as { apiKey?: unknown };
          if (typeof payload.apiKey === "string") clientApiKey = payload.apiKey;
        } catch {
          // Invalid client payloads are rejected below.
        }
        if (!authorized(clientApiKey)) throw new Error("You are not authorized to upload CSV files.");
        if (!BLOB_PATH.test(pathname)) throw new Error("The CSV upload path is invalid.");

        return {
          addRandomSuffix: false,
          allowedContentTypes: ["text/csv", "application/vnd.ms-excel", "application/octet-stream"],
          maximumSizeInBytes: MAX_UPLOAD_BYTES,
          tokenPayload: "geocheck-csv-v1",
        };
      },
      onUploadCompleted: async ({ blob, tokenPayload }) => {
        if (tokenPayload !== "geocheck-csv-v1" || !BLOB_PATH.test(blob.pathname)) {
          throw new Error("The uploaded CSV metadata is invalid.");
        }
      },
    });

    return Response.json(result);
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "CSV upload authorization failed." },
      { status: 400 },
    );
  }
}
