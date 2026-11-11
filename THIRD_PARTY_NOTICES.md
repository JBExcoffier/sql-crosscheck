# Third-party notices

Original project code is licensed under the root [MIT license](LICENSE).
The bundled third-party materials retain their upstream copyrights and terms,
reproduced below. Existing notices within source files remain unchanged.

## Microsoft AdventureWorks

The sample data in `data/adventure-works/csv-files/` and the original SQL Server
installer in `data/adventure-works/installer/original_installer.sql` originate
from Microsoft's AdventureWorks samples. The CSVs include adaptations for
PostgreSQL. The generated schema in
`documentation/databases/adventureworks/schema.yaml` describes this sample database.

Source: [Microsoft SQL Server samples — AdventureWorks](https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works).
Upstream license: [Microsoft SQL Server Sample Code](https://github.com/microsoft/sql-server-samples/blob/master/license.txt).

```text
Microsoft SQL Server Sample Code

Copyright (c) Microsoft Corporation

All rights reserved.

MIT License.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## AdventureWorks for Postgres

`data/adventure-works/installer/install.sql` and `update_csvs.rb` are adapted
from Lorin Thwaits' AdventureWorks for Postgres project. Local changes include
CSV paths and data-import adjustments. These scripts adapt Microsoft's
AdventureWorks database for PostgreSQL.

Source: [AdventureWorks for Postgres](https://github.com/lorint/AdventureWorks-for-Postgres).
Upstream license: [MIT license](https://github.com/lorint/AdventureWorks-for-Postgres/blob/master/LICENSE).

```text
Copyright (c) 2020 Polangeles LTD.

All rights reserved.

MIT License.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
