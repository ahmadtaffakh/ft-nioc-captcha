import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const filePath = path.resolve(
  "outputs/01a1f4d2-8b37-7460-852a-9b6f2952b483/labels-completed-900.xlsx",
);
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(filePath));

for (const range of ["labels!A1:B5", "labels!A385:B389", "labels!A769:B773"]) {
  const result = await workbook.inspect({
    kind: "table",
    range,
    include: "values,formulas",
    tableMaxRows: 10,
    tableMaxCols: 2,
    maxChars: 3000,
  });
  console.log(result.ndjson);
}
