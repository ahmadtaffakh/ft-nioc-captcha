using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;
using System.Collections.Generic;

static class PargarProbe
{
    [STAThread]
    static void Main()
    {
        ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; // TLS 1.2 on legacy .NET targets.
        Console.WriteLine("Pargar connection diagnostic v12");
        Console.Write("Pargar base URL: ");
        Uri site;
        if (!Uri.TryCreate(Console.ReadLine(), UriKind.Absolute, out site) ||
            (site.Scheme != "http" && site.Scheme != "https") ||
            site.UserInfo != "" || site.Query != "" || site.Fragment != "")
        {
            Console.WriteLine("Enter an HTTP(S) base URL without credentials, query or fragment.");
            return;
        }
        if (site.Scheme == "http")
        {
            Console.Write("HTTP sends the API token in cleartext over the network. Continue (y/N)? ");
            if (!String.Equals(Console.ReadLine(), "y", StringComparison.OrdinalIgnoreCase)) return;
        }
        Console.Write("Username: ");
        string username = (Console.ReadLine() ?? "").Trim();
        Console.Write("API token (hidden): ");
        var secret = new StringBuilder();
        for (;;)
        {
            var key = Console.ReadKey(true);
            if (key.Key == ConsoleKey.Enter) break;
            if (key.Key == ConsoleKey.Backspace && secret.Length > 0) secret.Length--;
            if (!Char.IsControl(key.KeyChar)) secret.Append(key.KeyChar);
        }
        Console.WriteLine();
        string token = secret.ToString().Trim();
        if (username.Length == 0 || token.Length == 0) return;
        string basic = Convert.ToBase64String(Encoding.UTF8.GetBytes(username + ":" + token));
        string root = site.GetLeftPart(UriPartial.Path).TrimEnd('/');
        if (root.EndsWith("/mailbox.api", StringComparison.OrdinalIgnoreCase))
            root = root.Substring(0, root.LastIndexOf('/'));

        string report = Path.Combine(Path.GetTempPath(), "Pargar-Debug-" + Guid.NewGuid().ToString("N") + ".txt");
        using (var log = new StreamWriter(report, false, Encoding.UTF8))
        {
            log.WriteLine("Pargar diagnostic v12 | TLS 1.2 | " + DateTimeOffset.Now.ToString("o"));
            log.WriteLine("Windows: " + Environment.OSVersion.Version + " | CLR: " + Environment.Version);

            Probe(log, root + "/inf.Api/v0/Authentication/Authenticate", basic, username, token);
            var senders = new SortedDictionary<Guid, string>();
            var recipients = new SortedDictionary<Guid, string>();
            Probe(log, root + "/Mailbox.api/v0/messages/GetCurrentUserSenderContacts?searchText=&pageSize=100&pageNum=0", basic, username, token, senders);
            string recipientRoute = root + "/Mailbox.api/v0/messages/MessageReceivingCandidates";
            foreach (string name in new[] { "احمد مهرزاد", "مهرزاد احمد" })
            {
                log.WriteLine("Recipient search: " + name);
                Probe(log, recipientRoute + "?searchterm=" + Uri.EscapeDataString(name) +
                    "&pageSize=100&pageNum=0", basic, username, token, recipients);
            }
            if (site.Scheme == "https") SendTest(log, root, basic, senders, recipients);
        }
        Console.WriteLine("Report: " + report);
        Console.WriteLine("Press Enter to exit.");
        Console.ReadLine();
    }

    static void Probe(TextWriter log, string url, string basic, string username, string token, SortedDictionary<Guid, string> contacts = null)
    {
        var timer = Stopwatch.StartNew();
        string endpoint = url.Replace(Uri.EscapeDataString(token), "[redacted]").Replace(token, "[redacted]")
            .Replace(basic, "[redacted]").Replace(Uri.EscapeDataString(username), "[redacted]").Replace(username, "[redacted]");
        log.WriteLine("GET " + endpoint);
        var request = (HttpWebRequest)WebRequest.Create(url);
        request.AllowAutoRedirect = false;
        request.Timeout = 15000;
        request.ReadWriteTimeout = 15000;
        request.Accept = "application/json";
        request.Headers[HttpRequestHeader.Authorization] = "Basic " + basic;
        request.AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate;
        HttpWebResponse response = null;
        try
        {
            log.WriteLine("Proxy: " + (request.Proxy != null && !request.Proxy.IsBypassed(request.RequestUri)));
            try { response = (HttpWebResponse)request.GetResponse(); }
            catch (WebException error)
            {
                log.WriteLine("Transport: " + error.Status);
                for (Exception cause = error; cause != null; cause = cause.InnerException)
                    log.WriteLine("Exception: " + cause.GetType().Name + " | HRESULT: 0x" + cause.HResult.ToString("X8"));
                response = error.Response as HttpWebResponse;
            }
            if (response == null) return;
            log.WriteLine("HTTP " + (int)response.StatusCode + " | " + timer.ElapsedMilliseconds + " ms");
            Console.WriteLine("HTTP " + (int)response.StatusCode);
            log.WriteLine("Redirect header present: " + (response.Headers["Location"] != null));
            log.WriteLine("Authentication challenge present: " + (response.Headers["WWW-Authenticate"] != null));
            using (var stream = response.GetResponseStream())
            using (var reader = new StreamReader(stream, Encoding.UTF8))
            {
                var buffer = new char[65537];
                int length = reader.ReadBlock(buffer, 0, buffer.Length);
                string body = new string(buffer, 0, length).TrimStart();
                string type = body.Length == 0 ? "empty" : body.StartsWith("<") ? "markup" :
                    body.StartsWith("{") || body.StartsWith("[") ? "JSON-like" : "other";
                log.WriteLine("Body format: " + type + " | chars sampled: " + length);
                if (type == "JSON-like" && length < buffer.Length)
                {
                    var obj = new JavaScriptSerializer().DeserializeObject(body) as Dictionary<string, object>;
                    if ((int)response.StatusCode == 404)
                    {
                        string[] markers = {
                            "No HTTP resource was found", "No action was found",
                            "No route", "No type was found", "No controller",
                            "not found", "not authorized", "forbidden",
                            "permission", "access denied", "method not allowed",
                            "GetCurrentUserSenderContacts"
                        };
                        foreach (string marker in markers)
                        {
                            if (body.IndexOf(marker, StringComparison.OrdinalIgnoreCase) >= 0)
                                log.WriteLine("404 marker: " + marker);
                        }
                        if (obj != null)
                        {
                            foreach (string field in new[] { "Message", "MessageDetail", "Error", "ErrorCode", "ExceptionType", "title", "detail" })
                            {
                                if (obj.ContainsKey(field)) log.WriteLine("404 field present: " + field);
                            }
                        }
                    }
                    object value;
                    if ((int)response.StatusCode == 404 && obj != null &&
                        obj.TryGetValue("Message", out value) && value is string)
                    {
                        string message = ((string)value).Replace(Uri.EscapeDataString(token), "[redacted]")
                            .Replace(token, "[redacted]").Replace(basic, "[redacted]")
                            .Replace(Uri.EscapeDataString(username), "[redacted]").Replace(username, "[redacted]");
                        var visible = new StringBuilder();
                        foreach (char c in message)
                            visible.Append(Char.IsControl(c) ? ' ' : c);
                        Console.WriteLine("404 Message (local display only): " + visible);
                        Console.Write("Include the redacted 404 message in the debug report (y/N)? ");
                        if (String.Equals(Console.ReadLine(), "y", StringComparison.OrdinalIgnoreCase))
                        {
                            log.WriteLine("404 Message: " + visible);
                        }
                    }
                    if (obj != null && obj.TryGetValue("Succeeded", out value) && value is bool)
                        log.WriteLine("Succeeded: " + value);
                    if (obj != null && obj.TryGetValue("Entities", out value) && value is object[])
                    {
                        log.WriteLine("Entities in page: " + ((object[])value).Length);
                        object succeeded;
                        if (contacts != null && (int)response.StatusCode == 200 &&
                            obj.TryGetValue("Succeeded", out succeeded) && true.Equals(succeeded))
                        {
                            foreach (var item in (object[])value)
                            {
                                var entry = item as Dictionary<string, object>;
                                object id, label;
                                Guid contactId;
                                if (entry != null && (entry.TryGetValue("Contactid", out id) ||
                                    entry.TryGetValue("ContactId", out id)) && Guid.TryParse(id as string, out contactId) &&
                                    contactId != Guid.Empty && entry.TryGetValue("text", out label) && label is string)
                                    contacts[contactId] = (string)label;
                            }
                        }
                    }
                }
            }
        }
        catch (Exception error) { log.WriteLine("Diagnostic error type: " + error.GetType().Name); }
        finally
        {
            if (response != null) response.Dispose();
            log.WriteLine();
        }
    }

    static Guid Pick(string title, SortedDictionary<Guid, string> contacts)
    {
        var ids = new List<Guid>(contacts.Keys);
        Console.WriteLine(title);
        for (int i = 0; i < ids.Count; i++)
            Console.WriteLine((i + 1) + ". " + JsonLabel(contacts[ids[i]]) + " [" + ids[i] + "]");
        Console.Write("Select number or Enter to cancel: ");
        int number;
        return Int32.TryParse(Console.ReadLine(), out number) && number > 0 && number <= ids.Count
            ? ids[number - 1] : Guid.Empty;
    }

    static string JsonLabel(string label)
    {
        var output = new StringBuilder();
        foreach (char c in label)
            output.Append(c < 32 || c > 126 ? "\\u" + ((int)c).ToString("X4") : c.ToString());
        return output.ToString();
    }

    static void SendTest(TextWriter log, string root, string basic,
        SortedDictionary<Guid, string> senders, SortedDictionary<Guid, string> recipients)
    {
        if (senders.Count == 0 || recipients.Count == 0)
        {
            Console.WriteLine("No validated sender or recipient is available.");
            return;
        }
        Console.Write("Test actual ZIP delivery (y/N)? ");
        if (!String.Equals(Console.ReadLine(), "y", StringComparison.OrdinalIgnoreCase)) return;
        Guid sender = Pick("Sender", senders);
        if (sender == Guid.Empty) return;
        Guid recipient = Pick("Recipient (verify the contact before sending)", recipients);
        if (recipient == Guid.Empty) return;
        Console.Write("ZIP file path: ");
        string path = (Console.ReadLine() ?? "").Trim().Trim('"');
        if (!String.Equals(Path.GetExtension(path), ".zip", StringComparison.OrdinalIgnoreCase) || !File.Exists(path))
            return;
        Console.Write("Message text UTF-8 file path: ");
        string textPath = (Console.ReadLine() ?? "").Trim().Trim('"');
        if (!File.Exists(textPath)) return;

        bool reserved = false;
        try
        {
            string message = File.ReadAllText(textPath, new UTF8Encoding(false, true));
            if (String.IsNullOrWhiteSpace(message)) return;
            using (var file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha = System.Security.Cryptography.SHA256.Create())
            {
                string hash = BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "");
                file.Position = 0;
                string filename = Path.GetFileName(path);
                Console.WriteLine("ZIP: " + filename + " | bytes: " + file.Length);
                Console.WriteLine("Recipient: " + JsonLabel(recipients[recipient]) + " [" + recipient + "]");
                Console.Write("Type SEND to deliver one real message: ");
                if (!String.Equals((Console.ReadLine() ?? "").Trim(), "SEND", StringComparison.OrdinalIgnoreCase))
                {
                    Console.WriteLine("Send canceled before contacting SendMail.");
                    log.WriteLine("SendMail canceled: confirmation missing");
                    return;
                }
                var json = new JavaScriptSerializer();
                string payload = json.Serialize(new {
                    Subject = filename, TextBody = message, RedirectLink = (string)null,
                    Sender = new { ContactId = sender.ToString() },
                    Receivers = new[] { new { ContactId = recipient.ToString() } },
                    CcReceivers = new object[0], BccReceivers = new object[0],
                    Priority = 0, Privacy = 0, ReferenceNumber = (string)null
                });
                string boundary = "----PargarTest" + Guid.NewGuid().ToString("N");
                string fallback = Encoding.ASCII.GetString(Encoding.ASCII.GetBytes(filename)).Replace('"', '_');
                byte[] prefix = Encoding.UTF8.GetBytes("--" + boundary +
                    "\r\nContent-Disposition: form-data; name=\"Message\"\r\nContent-Type: application/json; charset=utf-8\r\n\r\n" +
                    payload + "\r\n--" + boundary + "\r\nContent-Disposition: form-data; name=\"Attachments\"; filename=\"" +
                    fallback + "\"; filename*=UTF-8''" + Uri.EscapeDataString(filename) + "\r\nContent-Type: application/zip\r\n\r\n");
                byte[] suffix = Encoding.ASCII.GetBytes("\r\n--" + boundary + "--\r\n");
                var request = (HttpWebRequest)WebRequest.Create(root + "/Mailbox.api/v0/messages/sendmail");
                request.Method = "POST";
                request.AllowAutoRedirect = false;
                request.AllowWriteStreamBuffering = false;
                request.Timeout = request.ReadWriteTimeout = 30000;
                request.Headers[HttpRequestHeader.Authorization] = "Basic " + basic;
                request.Accept = "application/json";
                request.ContentType = "multipart/form-data; boundary=" + boundary;
                request.ContentLength = prefix.Length + file.Length + suffix.Length;
                string state = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "Pargar-Test", "send-state");
                Directory.CreateDirectory(state);
                string record = Path.Combine(state, hash + ".txt");
                try
                {
                    using (var marker = new FileStream(record, FileMode.CreateNew))
                    using (var writer = new StreamWriter(marker)) writer.WriteLine("Pending or uncertain");
                    reserved = true;
                }
                catch (IOException)
                {
                    if (!File.Exists(record)) throw;
                    Console.WriteLine("This ZIP already has a local send record. Check Pargar before any further action.");
                    log.WriteLine("SendMail skipped: existing local send record");
                    return;
                }
                using (var upload = request.GetRequestStream())
                {
                    upload.Write(prefix, 0, prefix.Length);
                    file.CopyTo(upload);
                    upload.Write(suffix, 0, suffix.Length);
                }
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    log.WriteLine("SendMail HTTP " + (int)response.StatusCode);
                    var result = json.DeserializeObject(reader.ReadToEnd()) as Dictionary<string, object>;
                    object success, id;
                    Guid mailId;
                    if ((int)response.StatusCode < 200 || (int)response.StatusCode >= 300 ||
                        result == null || !result.TryGetValue("Succeeded", out success) || !true.Equals(success) ||
                        !result.TryGetValue("MailId", out id) || !Guid.TryParse(id as string, out mailId) || mailId == Guid.Empty)
                        throw new InvalidDataException();
                    File.AppendAllText(record, "Confirmed MailId: " + mailId + Environment.NewLine);
                    log.WriteLine("SendMail confirmed. MailId: " + mailId);
                    Console.WriteLine("Delivery confirmed. MailId: " + mailId);
                }
            }
        }
        catch (Exception error)
        {
            var web = error as WebException;
            if (web != null && web.Response is HttpWebResponse)
            {
                using (var response = (HttpWebResponse)web.Response)
                    log.WriteLine("SendMail HTTP " + (int)response.StatusCode);
            }
            log.WriteLine("SendMail diagnostic: " + error.GetType().Name);
            Console.WriteLine(reserved ? "Delivery unconfirmed. Check Pargar. No automatic retry." : "Send was not started.");
        }
    }
}











