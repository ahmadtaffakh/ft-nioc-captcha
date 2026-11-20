import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve("outputs/01a1f4d2-8b37-7460-852a-9b6f2952b483");
const inputPath = path.join(root, "work", "manual_labels_900.csv");
const outputPath = path.join(root, "labels-completed-900.xlsx");
const csvText = await fs.readFile(inputPath, "utf8");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "labels" });
const sheet = workbook.worksheets.getItem("labels");

sheet.showGridLines = false;
sheet.freezePanes.freezeRows(1);
sheet.getRange("A1:B773").format.font = { name: "Arial", size: 10 };
sheet.getRange("A1:B1").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: "#1F4E78" },
};
sheet.getRange("A2:A773").format.columnWidth = 34;
sheet.getRange("B2:B773").format.columnWidth = 12;
sheet.getRange("B2:B773").format.numberFormat = "@";
sheet.getRange("A2:A773").format.horizontalAlignment = "left";
sheet.getRange("B2:B773").format.horizontalAlignment = "center";
sheet.getRange("A1:B773").format.verticalAlignment = "center";
sheet.getRange("A1:B773").format.rowHeight = 18;

workbook.recalculate();

const inspection = await workbook.inspect({
  kind: "table",
  range: "labels!A1:B773",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 2,
  maxChars: 4000,
});
console.log(inspection.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const topPreview = await workbook.render({
  sheetName: "labels",
  range: "A1:B30",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(
  path.join(root, "work", "labels-top-900.png"),
  new Uint8Array(await topPreview.arrayBuffer()),
);

const bottomPreview = await workbook.render({
  sheetName: "labels",
  range: "A744:B773",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(
  path.join(root, "work", "labels-bottom-900.png"),
  new Uint8Array(await bottomPreview.arrayBuffer()),
);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath }));
