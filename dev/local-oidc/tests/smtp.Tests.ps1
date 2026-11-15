Describe 'Local Keycloak SMTP' {
    BeforeEach {
        function docker { $global:LASTEXITCODE = 0 }
        . "$PSScriptRoot/../stack.ps1" status
    }

    It 'uses the native exit code when login writes progress to stderr' {
        function docker {
            & cmd.exe /d /c "echo Logging into Keycloak 1>&2 & exit /b $nativeExitCode"
            $global:LASTEXITCODE = $LASTEXITCODE
        }

        $nativeExitCode = 0
        Test-LocalRealmAdminLogin 'login' | Should Be $true
        $nativeExitCode = 1
        Test-LocalRealmAdminLogin 'login' | Should Be $false
    }

    It 'sets the QQ sender and uses TLS and authentication' {
        $code = ConvertTo-SecureString 'qq-test-code' -AsPlainText -Force
        $calls = [System.Collections.Generic.List[string]]::new()
        function Invoke-RealmAdmin($Arguments, $Body) {
            $calls.Add('update')
            ($Arguments -join ' ') | Should Be 'update realms/travelmind-dev -n'
            $Body.Count | Should Be 1
            $Body.smtpServer.host | Should Be 'smtp.qq.com'
            $Body.smtpServer.port | Should Be '465'
            $Body.smtpServer.ssl | Should Be 'true'
            $Body.smtpServer.starttls | Should Be 'false'
            $Body.smtpServer.auth | Should Be 'true'
            $Body.smtpServer.user | Should Be 'sender@qq.com'
            $Body.smtpServer.from | Should Be 'sender@qq.com'
            $Body.smtpServer.password | Should Be 'qq-test-code'
        }

        Update-LocalQqSmtp 'sender@qq.com' $code

        $calls.Count | Should Be 1
    }

    It 'retains a configured SMTP server during an account upgrade' {
        $updates = [System.Collections.Generic.List[hashtable]]::new()
        function Invoke-RealmAdmin($Arguments, $Body) {
            switch ($Arguments -join ' ') {
                'get authentication/flows -r travelmind-dev --fields alias' { '[{"alias":"travelmind-reset"}]' }
                'get authentication/flows/travelmind-reset/executions -r travelmind-dev' {
                    '[{"providerId":"reset-credential-email","authenticationConfig":"reset-config"}]'
                }
                'get realms/travelmind-dev' { '{"smtpServer":{"host":"smtp.qq.com"}}' }
                'get clients -r travelmind-dev -q clientId=travelmind-local' {
                    '[{"id":"client-id","clientId":"travelmind-local"}]'
                }
                'update realms/travelmind-dev -n' { $updates.Add($Body) }
            }
        }

        Update-LocalRealm

        $updates.Count | Should Be 1
        $updates[0].ContainsKey('smtpServer') | Should Be $false
    }
}
